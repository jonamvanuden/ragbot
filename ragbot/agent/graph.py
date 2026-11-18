"""One LangGraph for everything: the router decides, then RAG, the toolbox agent or code mode answers.

router  – call 1: "wissen" or "agent", and for the agent "toolbox" or "code" (skipped if the user picked a mode)
rag     – searches the knowledge base and streams a cited answer
agent   – LLM picks the next tool as JSON (tool + args, or "fertig")
approve – write tools pause here (LangGraph interrupt) until a human says yes or no
act     – runs the tool via ragbot.tools.call_tool (logged in api_calls)
code    – LLM writes a Python script against the SDK, it runs in a sandbox, errors are fed back.
          Reading is direct (read-only DB); write methods only PLAN changes.
approve_plan – if the script planned changes: one approval for the whole plan (interrupt)
execute_plan – runs every planned change with the normal toolbox tool (rules + audit log)
respond – LLM writes the final German answer from all step results (streamed)

START → router ─┬─ wissen ────────→ rag ─→ END
                ├─ agent·code ────→ code ─┬─ no plan ────────────────────────────→ respond ─→ END
                │                         └─ plan → approve_plan ─┬─ yes → execute_plan ─→ respond
                │                                                 └─ no ─────────────────→ respond
                └─ agent·toolbox ─→ agent ─┬─ read tool ──────────────→ act ──→ agent
                                           ├─ write tool → approve ─┬─ yes ─→ act
                                           │                        └─ no ──→ agent
                                           └─ "fertig" ─→ respond ─→ END
"""
import operator
import uuid
from typing import Annotated, Iterator, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from ragbot.agent import prompts
from ragbot.llms import get_model
from ragbot.tools import TOOLS, call_tool
from ragbot.agent import router as agent_router
from ragbot.codemode.coder import solve as write_and_run_code
from ragbot.tools import store
from ragbot import config
from ragbot.rag.retriever import augment, retrieve_traced


MAX_STEPS = 6          # tool calls per question, then the agent must answer
   

class AgentState(TypedDict, total=False):
    question: str
    history: list[dict]                            # earlier chat turns (role/content)
    session_id: str | None
    steps: Annotated[list[dict], operator.add]     # every tool step, appended
    pending: dict | None                           # the tool call the agent wants to make next
    answer: str
    mode: str                                       # "auto" (router decides) | "agent" | "rag"
    route: str                                      # router's decision: "wissen" | "agent"
    modus: str                                      # for the agent: "toolbox" | "code"
    plan: list[dict] | None                         # changes a code-mode script planned, waiting for approval


def _emit(event: dict):
    """Send a live event to whoever is streaming the graph (CLI or web UI)."""
    try:
        get_stream_writer()(event)
    except Exception:
        pass

def router_node(state: AgentState) -> dict:
    mode = state.get("mode", "auto")
    if mode == "agent":
        r = {"route": "agent", "modus": "toolbox", "grund": "Agent-Modus manuell gewählt"}
    elif mode == "code":
        r = {"route": "agent", "modus": "code", "grund": "Code-Modus manuell gewählt"}
    elif mode == "rag":
        r = {"route": "wissen", "modus": "toolbox", "grund": "Fragen-Modus manuell gewählt"}
    else:
        r = agent_router.route(state["question"], state.get("history", []))
    _emit({"type": "route", **r})
    return {"route": r["route"], "modus": r.get("modus", "toolbox")}

def rag(state: AgentState) -> dict:
    """Classic knowledge-base answer: search, put the chunks into the prompt, stream the reply."""
    question = state["question"]
    hits, trace = retrieve_traced(question)
    prompt = augment(question, hits)
    _emit({"type": "search", "hits": trace["hits"], "prompt": prompt,
           "ms": round(trace["embed_ms"] + trace["search_ms"], 1)})
    _emit({"type": "sources", "items": [{"n": i, "title": h.title, "source": h.source, "score": round(h.score, 3)}
                                        for i, h in enumerate(hits, 1)]})
    history = [{"role": m["role"], "content": m["content"]} for m in state.get("history", [])[-10:]]
    messages = [{"role": "system", "content": config.SYSTEM_PROMPT}] + history + [{"role": "user", "content": prompt}]
    parts = []
    for ev in get_model().stream(messages):
        if ev.kind in ("think", "answer"):
            _emit({"type": ev.kind, "text": ev.text})
        elif ev.kind == "cutoff":
            _emit({"type": "cutoff"})
        elif ev.kind == "usage":
            _emit({"type": "usage", **(ev.data or {})})
        if ev.kind == "answer":
            parts.append(ev.text)
    return {"answer": "".join(parts).strip()}


# ---------------- nodes ----------------
def code(state: AgentState) -> dict:
    """Code mode: write a script, run it in the sandbox, fix it on errors (see ragbot/codemode/coder.py)."""
    session_id = state.get("session_id")

    def on_attempt(a: dict):
        # every attempt goes into the audit log like a tool call, with the full script
        call_id = store.start("code_ausfuehren", "Code-Modus", "read", "agent", session_id,
                              {"plan": a["plan"], "code": a["code"]})
        store.finish(call_id, "ok" if a["ok"] else "error", a["ms"],
                     {"ausgabe": a["output"]} if a["ok"] else None, a["error"])
        _emit({"type": "code", "n": a["n"], "plan": a["plan"], "code": a["code"], "ok": a["ok"],
               "output": a["output"][:3000], "error": a["error"], "ms": a["ms"], "call_id": call_id})

    r = write_and_run_code(state["question"], state.get("history", []), on_attempt=on_attempt,
                           validate=validate_plan)
    step = {"tool": "code_ausfuehren", "args": {"plan": r["attempts"][-1]["plan"]},
            "status": "ok" if r["ok"] else "error", "gedanke": ""}
    if r["ok"]:
        step["result"] = r["output"][:4000]
    else:
        step["error"] = f"Code nach {len(r['attempts'])} Versuchen fehlgeschlagen: {r['error']}"
    return {"steps": [step], "plan": r["plan"] or None}


def validate_plan(plan: list[dict]) -> str | None:
    """Check every planned change against the tool's parameter model BEFORE the user is asked."""
    problems = []
    for i, p in enumerate(plan, 1):
        t = TOOLS.get(p["tool"])
        if t is None or t.kind != "write":
            problems.append(f"Änderung {i}: {p['tool']} ist keine erlaubte Änderung.")
            continue
        try:
            t.params.model_validate(p["args"])
        except ValidationError as e:
            problems.append(f"Änderung {i} ({p['tool']}): "
                            + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
    return "\n".join(problems) or None


def approve_plan(state: AgentState) -> dict:
    """One approval for all changes the script planned. Pauses the graph like approve()."""
    plan = state["plan"]
    decision = interrupt({"tool": "aenderungsplan", "product": "Code-Modus", "args": {},
                          "description": f"Die Auswertung möchte {len(plan)} Änderung(en) vornehmen.",
                          "gedanke": "", "plan": [{"tool": p["tool"], "text": p["text"]} for p in plan]})
    if isinstance(decision, dict) and decision.get("approved"):
        return {}
    _emit({"type": "rejected", "tool": "aenderungsplan"})
    return {"plan": None,
            "steps": [{"tool": "aenderungsplan", "args": {"anzahl": len(plan)}, "gedanke": "",
                       "status": "abgelehnt", "error": "Vom Benutzer abgelehnt, nichts wurde geändert."}]}


def execute_plan(state: AgentState) -> dict:
    """Run every approved change with the normal tool – same rules and audit log as the toolbox."""
    steps = []
    for p in state["plan"]:
        _emit({"type": "tool_start", "tool": p["tool"], "args": p["args"], "kind": "write"})
        out = call_tool(p["tool"], p["args"], caller="code", session_id=state.get("session_id"))
        _emit({"type": "tool_result", **out})
        step = {"tool": p["tool"], "args": p["args"], "gedanke": "", "status": out["status"],
                "call_id": out.get("call_id")}
        if out["status"] == "ok":
            step["result"] = out["result"]
        else:
            step["error"] = out.get("error")
        steps.append(step)
    return {"plan": None, "steps": steps}


def agent(state: AgentState) -> dict:
    steps = state.get("steps", [])
    if len(steps) >= MAX_STEPS:
        _emit({"type": "thought", "text": f"Schrittlimit ({MAX_STEPS}) erreicht, ich fasse zusammen."})
        return {"pending": None}
    try:
        d = get_model().ask_json(prompts.decide_messages(state), prompts.decision_schema())
    except Exception as e:
        _emit({"type": "thought", "text": f"Konnte keinen Schritt planen: {e}"})
        return {"pending": None}

    thought = str(d.get("gedanke", "")).strip()
    if thought:
        _emit({"type": "thought", "text": thought})
    if d.get("aktion") != "tool" or d.get("tool") not in TOOLS:
        return {"pending": None}

    args = d.get("args") if isinstance(d.get("args"), dict) else {}
    if any(s["tool"] == d["tool"] and s["args"] == args for s in steps):     # loop guard
        _emit({"type": "thought", "text": "Diesen Schritt habe ich schon gemacht, ich fasse zusammen."})
        return {"pending": None}
    return {"pending": {"tool": d["tool"], "args": args, "gedanke": thought}}


def approve(state: AgentState) -> dict:
    p = state["pending"]
    t = TOOLS[p["tool"]]
    # Pauses the graph. The value below goes to the UI; the run continues with resume(...).
    decision = interrupt({"tool": p["tool"], "product": t.product, "args": p["args"],
                          "description": " ".join(t.description.split()), "gedanke": p.get("gedanke", "")})
    if isinstance(decision, dict) and decision.get("approved"):
        args = decision.get("args") if isinstance(decision.get("args"), dict) else p["args"]
        return {"pending": {**p, "args": args}}
    _emit({"type": "rejected", "tool": p["tool"]})
    return {"pending": None,
            "steps": [{"tool": p["tool"], "args": p["args"], "gedanke": p.get("gedanke", ""),
                       "status": "abgelehnt", "error": "Vom Benutzer abgelehnt, nicht ausgeführt."}]}


def act(state: AgentState) -> dict:
    p = state["pending"]
    _emit({"type": "tool_start", "tool": p["tool"], "args": p["args"], "kind": TOOLS[p["tool"]].kind})
    out = call_tool(p["tool"], p["args"], caller="agent", session_id=state.get("session_id"))
    _emit({"type": "tool_result", **out})
    step = {"tool": p["tool"], "args": p["args"], "gedanke": p.get("gedanke", ""),
            "status": out["status"], "call_id": out.get("call_id")}
    if out["status"] == "ok":
        step["result"] = out["result"]
    else:
        step["error"] = out.get("error")
    return {"pending": None, "steps": [step]}


def respond(state: AgentState) -> dict:
    parts = []
    for ev in get_model().stream(prompts.respond_messages(state)):
        if ev.kind in ("think", "answer"):
            _emit({"type": ev.kind, "text": ev.text})
        if ev.kind == "answer":
            parts.append(ev.text)
    return {"answer": "".join(parts).strip()}


# ---------------- routing ----------------
def after_router(state: AgentState) -> str:
    if state.get("route") == "wissen":
        return "rag"
    return "code" if state.get("modus") == "code" else "agent"
    
def after_code(state: AgentState) -> str:
    return "approve_plan" if state.get("plan") else "respond"


def after_plan_approval(state: AgentState) -> str:
    return "execute_plan" if state.get("plan") else "respond"


def after_agent(state: AgentState) -> str:
    p = state.get("pending")
    if not p:
        return "respond"
    return "approve" if TOOLS[p["tool"]].kind == "write" else "act"


def after_approve(state: AgentState) -> str:
    return "act" if state.get("pending") else "agent"


def build():
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("rag", rag)
    g.add_node("code", code)
    g.add_node("approve_plan", approve_plan)
    g.add_node("execute_plan", execute_plan)
    g.add_node("agent", agent)
    g.add_node("approve", approve)
    g.add_node("act", act)
    g.add_node("respond", respond)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", after_router, ["rag", "agent", "code"])
    g.add_conditional_edges("code", after_code, ["approve_plan", "respond"])
    g.add_conditional_edges("approve_plan", after_plan_approval, ["execute_plan", "respond"])
    g.add_edge("execute_plan", "respond")
    g.add_edge("rag", END)
    g.add_conditional_edges("agent", after_agent, ["approve", "act", "respond"])
    g.add_conditional_edges("approve", after_approve, ["act", "agent"])
    g.add_edge("act", "agent")
    g.add_edge("respond", END)
     
    return g.compile(checkpointer=MemorySaver())   # needed for interrupt/resume


GRAPH = build()


# ---------------- public API: start a run, resume after approval ----------------
def _run(inp, thread_id: str) -> Iterator[dict]:
    cfg = {"configurable": {"thread_id": thread_id}}
    paused = False
    for mode, chunk in GRAPH.stream(inp, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom":
            yield chunk
        elif "__interrupt__" in chunk:
            paused = True
            yield {"type": "approval", "thread_id": thread_id, **chunk["__interrupt__"][0].value}
    if not paused:
        yield {"type": "done", "thread_id": thread_id}

def start(question: str, history: list[dict] | None = None, session_id: str | None = None, mode: str = "auto") -> Iterator[dict]:
    """New question. Yields events; ends with 'done' or 'approval' (then call resume)."""
    thread_id = str(uuid.uuid4())
    yield {"type": "start", "thread_id": thread_id}
    yield from _run({"question": question, "history": history or [], "session_id": session_id, "mode": mode,
                     "steps": [], "pending": None, "plan": None}, thread_id)


def resume(thread_id: str, approved: bool, args: dict | None = None) -> Iterator[dict]:
    """Continue a paused run with the human's decision."""
    yield from _run(Command(resume={"approved": approved, "args": args}), thread_id)