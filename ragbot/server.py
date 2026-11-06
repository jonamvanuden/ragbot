import json
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ragbot import chatlog, config, dbview, tools
from ragbot.agent import graph as agent_graph 
from ragbot.llms import get_model
from ragbot.rag import store
from ragbot.rag.embedder import embed
from ragbot.rag.retriever import augment, retrieve_traced
from ragbot.tools import browse as tool_browse

STATIC = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ragbot")


@asynccontextmanager
async def lifespan(app):
    get_model().load()          # load LLM weights at startup, not on the first message
    embed(["warmup"])           # load the embedding model at startup too
    yield
    store.close()               # close Qdrant cleanly on Ctrl+C


app = FastAPI(lifespan=lifespan)


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    messages: list[Msg]
    session_id: str | None = None


def log_retrieval(question: str, trace: dict):
    lines = [f"[search] {question[:80]!r}  embed {trace['embed_ms']}ms  search {trace['search_ms']}ms"]
    for h in trace["hits"]:
        mark = "✓" if h["kept"] else "✗"
        lines.append(f"    {mark} {h['score']:.3f}  {h['title'][:50]} #{h['chunk']}")
    if not any(h["kept"] for h in trace["hits"]):
        lines.append(f"    → nothing above MIN_SCORE={trace['min_score']}, model gets no context")
    log.info("\n".join(lines))


# ---------------- chat ----------------
@app.post("/api/chat")
def chat(body: ChatIn):
    t_start = time.perf_counter()
    model = get_model()
    history = [m.model_dump() for m in body.messages]

    # ---- RAG ----
    question = history[-1]["content"]
    hits, trace = retrieve_traced(question)
    history[-1]["content"] = augment(question, hits)
    sources = [
        {"n": i, "title": h.title, "source": h.source, "score": round(h.score, 3)}
        for i, h in enumerate(hits, 1)
    ]
    log_retrieval(question, trace)

    messages = [{"role": "system", "content": config.SYSTEM_PROMPT}] + history

    def events():
        answer, thinking, usage = [], [], {}
        status, error = "aborted", None          # "aborted" = browser closed mid-answer
        t_gen = time.perf_counter()
        t_first = None

        yield json.dumps({"type": "sources", "items": sources}) + "\n"
        try:
            for ev in model.stream(messages):
                if ev.kind == "usage":
                    usage = ev.data or {}
                    continue
                if ev.kind in ("think", "answer") and t_first is None:
                    t_first = time.perf_counter()
                if ev.kind == "answer":
                    answer.append(ev.text)
                elif ev.kind == "think":
                    thinking.append(ev.text)
                elif ev.kind in ("done", "cutoff"):
                    status = ev.kind
                yield json.dumps({"type": ev.kind, "text": ev.text}) + "\n"
        except Exception as e:
            status, error = "error", str(e)
            yield json.dumps({"type": "error", "text": error}) + "\n"
        finally:
            t_end = time.perf_counter()
            completion = usage.get("completion_tokens")
            tps = (round(completion / (t_end - t_first), 1)
                   if completion and t_first and t_end > t_first else None)
            turn_id = chatlog.save({
                "session_id": body.session_id,
                "model": model.name,
                "question": question,
                "prompt": history[-1]["content"],
                "answer": "".join(answer),
                "thinking": "".join(thinking) or None,
                "status": status,
                "error": error,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": completion,
                "retrieval_ms": round(trace["embed_ms"] + trace["search_ms"], 1),
                "ttft_ms": round((t_first - t_gen) * 1000) if t_first else None,
                "generation_ms": round((t_end - t_gen) * 1000),
                "total_ms": round((t_end - t_start) * 1000),
                "tokens_per_s": tps,
                "hits": trace["hits"],
            })
            log.info(f"[chat #{turn_id}] {model.name}  {status}  "
                     f"{usage.get('prompt_tokens', '?')}+{completion or '?'} tok  "
                     f"first token {((t_first - t_gen) if t_first else 0):.2f}s  "
                     f"total {t_end - t_start:.1f}s  {tps or '?'} tok/s")

    return StreamingResponse(events(), media_type="application/x-ndjson")


# ---------------- log viewer API (logs.html) ----------------
@app.get("/api/logs")
def logs(limit: int = 50, offset: int = 0, q: str | None = None, status: str | None = None,
         model: str | None = None, session: str | None = None):
    return {**chatlog.list_turns(limit, offset, q, status, model, session), "stats": chatlog.stats()}


@app.get("/api/logs/{turn_id}")
def log_detail(turn_id: int):
    turn = chatlog.get_turn(turn_id)
    if not turn:
        raise HTTPException(404, "Turn not found")
    return turn


# ---------------- monitoring API (dashboard.html) ----------------
@app.get("/api/dashboard")
def dashboard(period: str = "7d"):
    return dbview.dashboard(period)


@app.get("/api/db/tables")
def db_tables():
    return dbview.tables()


@app.get("/api/db/rows")
def db_rows(table: str, limit: int = 50, offset: int = 0, order: str | None = None, desc: bool = True):
    try:
        return dbview.rows(table, limit, offset, order, desc)
    except (ValueError, sqlite3.Error) as e:
        raise HTTPException(400, str(e))


class SqlIn(BaseModel):
    sql: str


@app.post("/api/db/query")
def db_query(body: SqlIn):
    try:
        return dbview.query(body.sql)
    except (ValueError, sqlite3.Error) as e:
        raise HTTPException(400, str(e))

# ---------------- agent API (LangGraph) ----------------
AGENT_RUNS: dict[str, dict] = {}


class AgentResumeIn(BaseModel):
    thread_id: str
    approved: bool
    args: dict | None = None


def _save_agent_turn(run: dict):
    steps = [f"💭 {t}" for t in run["thoughts"]] + [
        f"🔧 {x['tool']} → {x['status']} (api_calls #{x['call_id']})" for x in run["tools"]]
    elapsed = time.perf_counter() - run["t0"] - run["waited"]          # without the human's thinking time
    turn_id = chatlog.save({
        "session_id": run["session_id"],
        "model": f"{run['model']} · agent",
        "question": run["question"],
        "prompt": "\n".join(steps) or "(keine Schritte)",
        "answer": "".join(run["answer"]),
        "status": run["status"],
        "error": run.get("error"),
        "total_ms": round(elapsed * 1000),
        "hits": run["hits"],
    })
    log.info(f"[agent #{turn_id}] {run['status']}  {len(run['tools'])} tools  {elapsed:.1f}s")


def _agent_stream(events, run: dict) -> StreamingResponse:
    def gen():
        try:
            for ev in events:
                t = ev["type"]
                if t == "start":
                    run["thread_id"] = ev["thread_id"]
                    AGENT_RUNS[ev["thread_id"]] = run
                elif t == "thought":
                    run["thoughts"].append(ev["text"])
                elif t == "answer":
                    run["answer"].append(ev["text"])
                elif t == "tool_result":
                    run["tools"].append({"tool": ev["tool"], "status": ev["status"], "call_id": ev.get("call_id")})
                    if ev["tool"] == "wissen_suchen" and ev["status"] == "ok":
                        run["hits"] += [{"title": h["titel"], "source": h["quelle"], "chunk": "–",
                                         "score": h["score"], "kept": True, "preview": h["text"][:300]}
                                        for h in ev["result"]["treffer"]]
                elif t == "approval":
                    run["wait_start"] = time.perf_counter()
                yield json.dumps(ev, default=str) + "\n"
                if t == "done":
                    run["status"] = "done"
                    _save_agent_turn(run)
                    AGENT_RUNS.pop(run.get("thread_id"), None)
        except Exception as e:
            log.exception("agent run failed")
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"
            run["status"], run["error"] = "error", str(e)
            _save_agent_turn(run)
            AGENT_RUNS.pop(run.get("thread_id"), None)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/agent/chat")
def agent_chat(body: ChatIn):
    history = [m.model_dump() for m in body.messages]
    question = history[-1]["content"]
    run = {"question": question, "session_id": body.session_id, "model": get_model().name,
           "t0": time.perf_counter(), "waited": 0.0, "status": "aborted",
           "thoughts": [], "tools": [], "answer": [], "hits": []}
    return _agent_stream(agent_graph.start(question, history[:-1], body.session_id), run)


@app.post("/api/agent/resume")
def agent_resume(body: AgentResumeIn):
    run = AGENT_RUNS.get(body.thread_id)
    if run is None:
        raise HTTPException(404, "Dieser Agent-Lauf ist nicht mehr aktiv. Bitte die Anfrage neu stellen.")
    if "wait_start" in run:
        run["waited"] += time.perf_counter() - run.pop("wait_start")
    return _agent_stream(agent_graph.resume(body.thread_id, body.approved, body.args), run)

# ---------------- tool call viewer API (tools.html) ----------------
@app.get("/api/calls")
def calls(limit: int = 50, offset: int = 0, q: str | None = None, tool: str | None = None,
          status: str | None = None, kind: str | None = None, caller: str | None = None):
    return tool_browse.list_calls(limit, offset, q, tool=tool, status=status, kind=kind, caller=caller)


@app.get("/api/calls-catalog")
def calls_catalog():
    return tool_browse.catalog()


@app.get("/api/calls/{call_id}")
def call_detail(call_id: int):
    c = tool_browse.get_call(call_id)
    if not c:
        raise HTTPException(404, "Aufruf nicht gefunden")
    return c


# must stay last: catches every other URL and serves the static files
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")