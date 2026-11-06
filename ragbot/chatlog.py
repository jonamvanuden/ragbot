"""Chat log in SQLite: every turn with question, prompt, answer, tokens, timings, search hits.

    python -m ragbot.chatlog            # last 20 turns
    python -m ragbot.chatlog 50         # last 50 turns
    python -m ragbot.chatlog --id 12    # full details of turn 12
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone

from ragbot import config

DB = config.DATA_DIR / "logs" / "chat.db"
COLUMNS = [
    "ts", "session_id", "model", "question", "prompt", "answer", "thinking", "status", "error",
    "prompt_tokens", "completion_tokens",
    "retrieval_ms", "ttft_ms", "generation_ms", "total_ms", "tokens_per_s",
    "hits",
]


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE IF NOT EXISTS turns "
                 f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(COLUMNS)})")
    return conn


# ---------------- write ----------------
def save(rec: dict) -> int:
    rec = {**rec,
           "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "hits": json.dumps(rec.get("hits", []), ensure_ascii=False)}
    conn = _connect()
    try:
        with conn:   # commits
            cur = conn.execute(
                f"INSERT INTO turns ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                [rec.get(c) for c in COLUMNS],
            )
        return cur.lastrowid
    finally:
        conn.close()


# ---------------- read (used by the web page) ----------------
def list_turns(limit=50, offset=0, q=None, status=None, model=None, session=None) -> dict:
    where, args = [], []
    if q:
        where.append("(question LIKE ? OR answer LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if status:
        where.append("status = ?")
        args.append(status)
    if model:
        where.append("model = ?")
        args.append(model)
    if session:
        where.append("session_id = ?")
        args.append(session)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""

    conn = _connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM turns {sql_where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, ts, session_id, model, status, question, prompt_tokens, completion_tokens, "
            f"retrieval_ms, ttft_ms, total_ms, tokens_per_s, hits "
            f"FROM turns {sql_where} ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        models = [r[0] for r in conn.execute("SELECT DISTINCT model FROM turns ORDER BY model")]
    finally:
        conn.close()

    items = []
    for r in rows:
        d = dict(r)
        hits = json.loads(d.pop("hits") or "[]")
        d["kept"] = sum(1 for h in hits if h["kept"])
        d["top_score"] = max((h["score"] for h in hits), default=None)
        items.append(d)
    return {"total": total, "items": items, "models": models}


def get_turn(turn_id: int) -> dict | None:
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = dict(r)
    d["hits"] = json.loads(d["hits"] or "[]")
    return d


def stats() -> dict:
    conn = _connect()
    try:
        rows = conn.execute("SELECT status, total_ms, ttft_ms, tokens_per_s, hits FROM turns").fetchall()
    finally:
        conn.close()

    def avg(values):
        values = [v for v in values if v]
        return round(sum(values) / len(values), 1) if values else None

    return {
        "count": len(rows),
        "avg_total_ms": avg(r["total_ms"] for r in rows),
        "avg_ttft_ms": avg(r["ttft_ms"] for r in rows),
        "avg_tps": avg(r["tokens_per_s"] for r in rows),
        "no_context": sum(1 for r in rows
                          if not any(h["kept"] for h in json.loads(r["hits"] or "[]"))),
        "problems": sum(1 for r in rows if r["status"] in ("error", "cutoff", "aborted")),
    }


# ---------------- terminal viewer ----------------
def _list(n: int):
    print(f"{'id':>5}  {'time (UTC)':19}  {'status':7}  {'tokens':>10}  {'search':>7}  {'total':>6}  question")
    for r in reversed(list_turns(limit=n)["items"]):
        tokens = f"{r['prompt_tokens'] or 0}+{r['completion_tokens'] or 0}"
        print(f"{r['id']:>5}  {r['ts'][:19]:19}  {r['status'] or '':7}  {tokens:>10}  "
              f"{(r['retrieval_ms'] or 0):5.0f}ms  {(r['total_ms'] or 0) / 1000:5.1f}s  "
              f"{(r['question'] or '')[:60]}")


def _show(turn_id: int):
    r = get_turn(turn_id)
    if not r:
        print(f"Turn {turn_id} nicht gefunden.")
        return
    print(f"#{r['id']}  {r['ts']}  model={r['model']}  session={r['session_id']}  status={r['status']}")
    print(f"tokens: {r['prompt_tokens']} prompt + {r['completion_tokens']} answer  |  "
          f"search {r['retrieval_ms']} ms  |  first token {r['ttft_ms']} ms  |  "
          f"generation {r['generation_ms']} ms  |  total {r['total_ms']} ms  |  {r['tokens_per_s']} tok/s")
    if r["error"]:
        print(f"ERROR: {r['error']}")
    print("\n--- QUESTION ---\n" + (r["question"] or ""))
    print("\n--- VECTOR SEARCH ---")
    for h in r["hits"]:
        mark = "✓" if h["kept"] else "✗ (below MIN_SCORE)"
        print(f"  {h['score']:.3f} {mark}  {h['title']} #{h['chunk']}  {h['source']}")
    print("\n--- PROMPT SENT TO MODEL (last user turn) ---\n" + (r["prompt"] or ""))
    if r["thinking"]:
        print("\n--- THINKING ---\n" + r["thinking"])
    print("\n--- ANSWER ---\n" + (r["answer"] or ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=20)
    ap.add_argument("--id", type=int)
    args = ap.parse_args()
    _show(args.id) if args.id else _list(args.n)