"""The agent as a LangGraph state machine.

agent   – LLM picks the next step as JSON (tool + args, or "fertig")
approve – write tools pause here (LangGraph interrupt) until a human says yes or no
act     – runs the tool via ragbot.tools.call_tool (logged in api_calls)
respond – LLM writes the final German answer from all step results (streamed)

START → agent ─┬─ read tool ──────────────→ act ──→ agent
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

from ragbot.agent import prompts
from ragbot.llms import get_model
from ragbot.tools import TOOLS, call_tool

MAX_STEPS = 6          # tool calls per question, then the agent must answer


class AgentState(TypedDict, total=False):
    question: str
    history: list[dict]                            # earlier chat turns (role/content)
    session_id: str | None
    steps: Annotated[list[dict], operator.add]     # every tool step, appended
    pending: dict | None                           # the tool call the agent wants to make next
    answer: str


def _emit(event: dict):
    """Send a live event to whoever is streaming the graph (CLI or web UI)."""
    try:
        get_stream_writer()(event)
    except Exception:
        pass


# ---------------- nodes ----------------
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
def after_agent(state: AgentState) -> str:
    p = state.get("pending")
    if not p:
        return "respond"
    return "approve" if TOOLS[p["tool"]].kind == "write" else "act"


def after_approve(state: AgentState) -> str:
    return "act" if state.get("pending") else "agent"


def build():
    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("approve", approve)
    g.add_node("act", act)
    g.add_node("respond", respond)
    g.add_edge(START, "agent")
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


def start(question: str, history: list[dict] | None = None, session_id: str | None = None) -> Iterator[dict]:
    """New question. Yields events; ends with 'done' or 'approval' (then call resume)."""
    thread_id = str(uuid.uuid4())
    yield {"type": "start", "thread_id": thread_id}
    yield from _run({"question": question, "history": history or [], "session_id": session_id,
                     "steps": [], "pending": None}, thread_id)


def resume(thread_id: str, approved: bool, args: dict | None = None) -> Iterator[dict]:
    """Continue a paused run with the human's decision."""
    yield from _run(Command(resume={"approved": approved, "args": args}), thread_id)
