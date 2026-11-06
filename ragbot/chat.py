import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ragbot import chatlog, config
from ragbot.llms import get_model
from ragbot.rag.embedder import embed
from ragbot.rag.retriever import augment, retrieve_traced

STATIC = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ragbot")


@asynccontextmanager
async def lifespan(app):
    get_model().load()          # load LLM weights at startup, not on the first message
    embed(["warmup"])           # load the embedding model at startup too
    yield


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


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")