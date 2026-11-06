"""Read-only queries on api_calls for the tool call viewer (tools.html)."""
import json

from ragbot.tools import store
from ragbot.tools.registry import TOOLS

FILTERS = ("tool", "status", "kind", "caller")


def list_calls(limit: int = 50, offset: int = 0, q: str | None = None, **filters) -> dict:
    where, args = [], []
    if q:
        where.append("(params LIKE ? OR result LIKE ? OR error LIKE ? OR session_id LIKE ?)")
        args += [f"%{q}%"] * 4
    for col in FILTERS:
        if filters.get(col):
            where.append(f"{col} = ?")
            args.append(filters[col])
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""

    conn = store._connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM api_calls {sql_where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, ts, tool, product, kind, caller, session_id, status, params, error, duration_ms "
            f"FROM api_calls {sql_where} ORDER BY id DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        callers = [r[0] for r in conn.execute("SELECT DISTINCT caller FROM api_calls WHERE caller IS NOT NULL ORDER BY 1")]
        s = conn.execute(
            "SELECT COUNT(*) AS n, SUM(status = 'ok') AS ok, SUM(status = 'error') AS error, "
            "SUM(status = 'invalid') AS invalid, SUM(status = 'ok' AND kind = 'write') AS changes, "
            "ROUND(AVG(duration_ms), 1) AS avg_ms FROM api_calls").fetchone()
    finally:
        conn.close()
    return {
        "total": total,
        "items": [{**dict(r), "params": json.loads(r["params"] or "{}")} for r in rows],
        "callers": callers,
        "tools": list(TOOLS),
        "stats": {k: (s[k] or 0) for k in s.keys()},
    }


def get_call(call_id: int) -> dict | None:
    conn = store._connect()
    try:
        r = conn.execute("SELECT * FROM api_calls WHERE id = ?", (call_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = dict(r)
    d["params"] = json.loads(d["params"] or "{}")
    d["result"] = json.loads(d["result"]) if d["result"] else None
    t = TOOLS.get(d["tool"])
    d["description"] = " ".join(t.description.split()) if t else None
    return d


def catalog() -> list[dict]:
    """Every tool with its parameter definitions (from the Pydantic schema) and call counts."""
    conn = store._connect()
    try:
        counts = {r["tool"]: dict(r) for r in conn.execute(
            "SELECT tool, COUNT(*) AS n, SUM(status = 'ok') AS ok, SUM(status IN ('error', 'invalid')) AS failed, "
            "MAX(ts) AS last FROM api_calls GROUP BY tool")}
    finally:
        conn.close()
    out = []
    for t in TOOLS.values():
        schema = t.params.model_json_schema()
        required = set(schema.get("required", []))
        params = []
        for name, p in schema.get("properties", {}).items():
            variants = [v for v in p.get("anyOf", [p]) if v.get("type") != "null"]
            v = variants[0] if variants else p
            typ = " | ".join(map(str, v["enum"])) if "enum" in v else v.get("format") or v.get("type", "any")
            params.append({"name": name, "type": typ, "required": name in required,
                           "default": p.get("default"), "description": p.get("description")})
        c = counts.get(t.name, {})
        out.append({"name": t.name, "product": t.product, "kind": t.kind,
                    "description": " ".join(t.description.split()), "params": params,
                    "calls": c.get("n", 0), "ok": c.get("ok", 0) or 0, "failed": c.get("failed", 0) or 0,
                    "last": c.get("last")})
    return out
