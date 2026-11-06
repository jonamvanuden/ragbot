"""Read-only views on the chat log for the monitoring page (dashboard.html).

Everything here only READS. The raw SQL console opens the database in
read-only mode, so even a typo like DELETE cannot change anything.
"""
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ragbot import chatlog, config

TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/Berlin"))      # buckets + labels in local time
PERIODS = {
    "24h": (timedelta(hours=24), "hour"),
    "7d": (timedelta(days=7), "day"),
    "30d": (timedelta(days=30), "day"),
    "all": (None, "day"),
}
STATUSES = ["done", "cutoff", "error", "aborted"]
READ_ONLY = re.compile(r"^\s*(select|with|pragma|explain)\b", re.I)
MAX_ROWS = 1000


# ---------------- helpers ----------------
def _ro() -> sqlite3.Connection:
    """Read-only connection. Creates the DB first if it doesn't exist yet."""
    chatlog._connect().close()
    conn = sqlite3.connect(f"file:{chatlog.DB}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _pct(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def _floor(dt: datetime, unit: str) -> datetime:
    dt = dt.replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=0) if unit == "day" else dt


def _cell(v):
    return f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else v


# ---------------- dashboard ----------------
def dashboard(period: str = "7d") -> dict:
    if period not in PERIODS:
        period = "7d"
    span, unit = PERIODS[period]
    now = datetime.now(TZ)

    with closing(_ro()) as conn:
        conn.row_factory = sqlite3.Row
        if span:
            cutoff = (now - span).astimezone(timezone.utc).isoformat(timespec="seconds")
            rows = conn.execute("SELECT * FROM turns WHERE ts >= ? ORDER BY id", (cutoff,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM turns ORDER BY id").fetchall()

    turns = []
    for r in rows:
        t = dict(r)
        try:
            t["dt"] = datetime.fromisoformat(t["ts"]).astimezone(TZ)
        except (TypeError, ValueError):
            continue
        t["hits"] = json.loads(t["hits"] or "[]")
        t["kept"] = [h for h in t["hits"] if h.get("kept")]
        t["top"] = max((h["score"] for h in t["hits"]), default=None)
        turns.append(t)

    done = [t for t in turns if t["status"] == "done"]
    no_ctx = [t for t in turns if not t["kept"] and "agent" not in (t["model"] or "")]

    # --- headline numbers ---
    tiles = {
        "requests": len(turns),
        "sessions": len({t["session_id"] for t in turns if t["session_id"]}),
        "avg_ms": _avg(t["total_ms"] for t in done),
        "p95_ms": _pct([t["total_ms"] for t in done], 0.95),
        "avg_ttft_ms": _avg(t["ttft_ms"] for t in done),
        "avg_tps": _avg(t["tokens_per_s"] for t in done),
        "tokens": sum((t["prompt_tokens"] or 0) + (t["completion_tokens"] or 0) for t in turns),
        "no_context": len(no_ctx),
        "no_context_rate": round(100 * len(no_ctx) / len(turns)) if turns else 0,
        "problems": sum(1 for t in turns if t["status"] in ("cutoff", "error", "aborted")),
    }

    # --- timeline (continuous: empty hours/days count as 0) ---
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    first = _floor(now - span, unit) if span else _floor(min((t["dt"] for t in turns), default=now), unit)
    buckets, cur = {}, first
    while cur <= _floor(now, unit):
        buckets[cur] = []
        cur = _floor(cur + step, unit)
    for t in turns:
        key = _floor(t["dt"], unit)
        if key in buckets:
            buckets[key].append(t)
    label_fmt = "%H:%M" if unit == "hour" else "%d.%m."
    timeline = []
    for key, ts in buckets.items():
        ok = [t["total_ms"] for t in ts if t["status"] == "done"]
        timeline.append({"label": key.strftime(label_fmt), "start": key.isoformat(), "count": len(ts),
                         "avg_ms": _avg(ok), "p95_ms": _pct(ok, 0.95)})

    # --- status mix ---
    status = Counter(t["status"] or "?" for t in turns)
    status = {s: status.get(s, 0) for s in STATUSES} | {k: v for k, v in status.items() if k not in STATUSES}

    # --- best search score per question (is the knowledge base answering?) ---
    edges = [round(0.25 + 0.05 * i, 2) for i in range(14)]                       # 0.25 … 0.90
    bins = ([{"label": "<0.25", "lo": 0.0, "hi": 0.25}]
            + [{"label": f"{a:.2f}", "lo": a, "hi": b} for a, b in zip(edges, edges[1:])]
            + [{"label": "≥0.90", "lo": 0.90, "hi": 9.0}])
    for b in bins:
        b["count"] = 0
        b["below_min"] = b["hi"] <= config.MIN_SCORE + 1e-9
    for t in turns:
        if t["top"] is None:
            continue
        for b in bins:
            if b["lo"] <= t["top"] < b["hi"]:
                b["count"] += 1
                break

    # --- which documents actually feed the answers ---
    used, titles = Counter(), {}
    for t in turns:
        for h in t["kept"]:
            used[h["source"]] += 1
            titles[h["source"]] = h["title"]
    sources = [{"source": s, "title": titles[s], "count": n} for s, n in used.most_common(10)]

    # --- per model ---
    by_model = defaultdict(list)
    for t in turns:
        by_model[t["model"] or "?"].append(t)
    models = [{
        "model": m,
        "requests": len(ts),
        "avg_ms": _avg(t["total_ms"] for t in ts if t["status"] == "done"),
        "avg_tps": _avg(t["tokens_per_s"] for t in ts if t["status"] == "done"),
        "tokens": sum((t["prompt_tokens"] or 0) + (t["completion_tokens"] or 0) for t in ts),
    } for m, ts in sorted(by_model.items(), key=lambda kv: -len(kv[1]))]

    def brief(t):
        return {"id": t["id"], "ts": t["ts"], "question": t["question"], "status": t["status"],
                "total_ms": t["total_ms"], "top_score": t["top"]}

    return {
        "period": period,
        "unit": unit,
        "min_score": config.MIN_SCORE,
        "generated": now.isoformat(timespec="seconds"),
        "tiles": tiles,
        "timeline": timeline,
        "status": status,
        "scores": {"bins": bins, "no_hits": sum(1 for t in turns if t["top"] is None)},
        "sources": sources,
        "models": models,
        "no_context": [brief(t) for t in reversed(no_ctx)][:10],
        "slowest": [brief(t) for t in sorted(done, key=lambda t: t["total_ms"] or 0, reverse=True)[:5]],
    }


# ---------------- raw database ----------------
def tables() -> dict:
    with closing(_ro()) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = []
        for name in names:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            out.append({"name": name, "columns": cols, "count": count})
    return {"db": str(chatlog.DB), "size_bytes": chatlog.DB.stat().st_size, "tables": out}


def rows(table: str, limit: int = 50, offset: int = 0, order: str | None = None, desc: bool = True) -> dict:
    meta = {t["name"]: t for t in tables()["tables"]}
    if table not in meta:                                   # whitelist: no SQL injection via names
        raise ValueError(f"Unbekannte Tabelle: {table}")
    cols = meta[table]["columns"]
    if order not in cols:
        order = "id" if "id" in cols else cols[0]
    limit = max(1, min(limit, 500))
    sql = f'SELECT * FROM "{table}" ORDER BY "{order}" {"DESC" if desc else "ASC"} LIMIT ? OFFSET ?'
    with closing(_ro()) as conn:
        data = conn.execute(sql, (limit, max(0, offset))).fetchall()
    return {"table": table, "columns": cols, "rows": [[_cell(v) for v in r] for r in data],
            "total": meta[table]["count"], "order": order, "desc": desc, "limit": limit, "offset": offset}


def query(sql: str) -> dict:
    sql = sql.strip().rstrip(";").strip()
    if not READ_ONLY.match(sql):
        raise ValueError("Nur lesende Abfragen sind erlaubt (SELECT, WITH, PRAGMA, EXPLAIN).")
    t0 = time.perf_counter()
    with closing(_ro()) as conn:
        cur = conn.execute(sql)                             # sqlite3 runs one statement only
        cols = [d[0] for d in (cur.description or [])]
        data = cur.fetchmany(MAX_ROWS + 1)
    return {"columns": cols, "rows": [[_cell(v) for v in r] for r in data[:MAX_ROWS]],
            "truncated": len(data) > MAX_ROWS, "ms": round((time.perf_counter() - t0) * 1000, 1)}