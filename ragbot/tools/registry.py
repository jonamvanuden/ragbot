"""Tool registry: one place that knows every tool, its parameters and how to call it.

Each tool is a plain function with a Pydantic parameter model. From that model we get
  - input validation (wrong types, missing fields → status 'invalid', nothing runs)
  - a JSON schema, which is exactly what LangGraph / CrewAI need to offer the tool to an LLM.
Every call, valid or not, is written to the api_calls table.
"""
import inspect
import time
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError

from ragbot.tools import store


class ToolError(Exception):
    """A business-rule failure the caller should see, e.g. 'end date before start date'."""


@dataclass
class Context:
    call_id: int            # row id in api_calls, also used to build mock record ids
    caller: str
    session_id: str | None


@dataclass
class Tool:
    name: str
    product: str
    kind: Literal["read", "write"]      # write = changes data → later: needs human approval
    description: str
    params: type[BaseModel]
    fn: Callable[[BaseModel, Context], dict]

    def schema(self) -> dict:
        """OpenAI-style function schema (used by LangGraph/CrewAI tool binding)."""
        return {"name": self.name, "description": self.description,
                "parameters": self.params.model_json_schema()}


TOOLS: dict[str, Tool] = {}


def tool(params: type[BaseModel], product: str, kind: Literal["read", "write"]):
    """Decorator: register a function as a tool. The docstring becomes the description."""
    def register(fn):
        TOOLS[fn.__name__] = Tool(fn.__name__, product, kind, inspect.cleandoc(fn.__doc__ or ""), params, fn)
        return fn
    return register


def describe() -> list[dict]:
    return [{"name": t.name, "product": t.product, "kind": t.kind, **t.schema()} for t in TOOLS.values()]


def call_tool(name: str, args: dict | None = None, caller: str = "manual",
              session_id: str | None = None) -> dict:
    """Validate, run, log. Always returns an envelope, never raises for tool problems."""
    if name not in TOOLS:
        return {"status": "error", "error": f"Unbekanntes Tool: {name}. Verfügbar: {', '.join(TOOLS)}"}
    t = TOOLS[name]
    args = {k: v for k, v in (args or {}).items() if v is not None}   # null = "not given" → default applies
    call_id = store.start(t.name, t.product, t.kind, caller, session_id, args)
    t0 = time.perf_counter()

    def done(status, result=None, error=None):
        store.finish(call_id, status, (time.perf_counter() - t0) * 1000, result, error)
        out = {"call_id": call_id, "tool": t.name, "status": status}
        out.update({"result": result} if result is not None else {"error": error})
        return out

    try:
        params = t.params.model_validate(args)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'eingabe'}: {err['msg']}" for err in e.errors())
        return done("invalid", error=msg)
    try:
        return done("ok", result=t.fn(params, Context(call_id, caller, session_id)))
    except ToolError as e:
        return done("error", error=str(e))
    except Exception as e:                       # bug in a tool: log it, don't crash the caller
        return done("error", error=f"{type(e).__name__}: {e}")