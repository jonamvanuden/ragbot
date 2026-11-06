"""The api_calls table: every tool call with parameters, result and status.

It lives in the same SQLite file as the chat log (data/logs/chat.db), so the
Monitoring page's database tab shows it automatically.
"""
import json
import sqlite3
from datetime import datetime, timezone

from ragbot import chatlog

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,       -- UTC, ISO 8601
    tool        TEXT,       -- e.g. urlaub_beantragen
    product     TEXT,       -- e.g. Urlaubsplaner
    kind        TEXT,       -- read | write
    caller      TEXT,       -- cli | http | agent | ...
    session_id  TEXT,
    params      TEXT,       -- JSON
    status      TEXT,       -- running | ok | invalid | error
    result      TEXT,       -- JSON
    error       TEXT,
    duration_ms REAL
)
"""


def _connect() -> sqlite3.Connection:
    chatlog.DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chatlog.DB)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def start(tool: str, product: str, kind: str, caller: str, session_id: str | None, params: dict) -> int:
    """Insert the call as 'running' and return its id (the id is used for mock record numbers)."""
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO api_calls (ts, tool, product, kind, caller, session_id, params, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'running')",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 tool, product, kind, caller, session_id, _dump(params)),
            )
        return cur.lastrowid
    finally:
        conn.close()


def finish(call_id: int, status: str, duration_ms: float, result=None, error: str | None = None):
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "UPDATE api_calls SET status = ?, result = ?, error = ?, duration_ms = ? WHERE id = ?",
                (status, _dump(result) if result is not None else None, error, round(duration_ms, 1), call_id),
            )
    finally:
        conn.close()


def successful(tool: str) -> list[dict]:
    """All successful calls of one tool, oldest first, with params/result parsed.
    The mock uses this as its 'database': a vacation request exists if it was made."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, ts, params, result FROM api_calls WHERE tool = ? AND status = 'ok' ORDER BY id",
            (tool,),
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "ts": r["ts"], "params": json.loads(r["params"] or "{}"),
             "result": json.loads(r["result"] or "{}")} for r in rows]


def recent(limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM api_calls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
