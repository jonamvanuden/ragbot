import time

from ragbot import config
from ragbot.rag import store
from ragbot.rag.embedder import embed
from ragbot.rag.schema import Hit


def retrieve_traced(query: str, k: int = config.TOP_K,
                    min_score: float = config.MIN_SCORE) -> tuple[list[Hit], dict]:
    """Like retrieve(), plus a trace for debugging: timings and ALL hits (kept and dropped)."""
    t0 = time.perf_counter()
    vector = embed([query])[0]
    t1 = time.perf_counter()
    hits = store.search(vector, k)
    t2 = time.perf_counter()

    kept = [h for h in hits if h.score >= min_score]
    trace = {
        "embed_ms": round((t1 - t0) * 1000, 1),
        "search_ms": round((t2 - t1) * 1000, 1),
        "min_score": min_score,
        "hits": [
            {"title": h.title, "source": h.source, "chunk": h.index,
             "score": round(h.score, 3), "kept": h.score >= min_score,
             "preview": h.text[:300]}
            for h in hits
        ],
    }
    return kept, trace


def retrieve(query: str, k: int = config.TOP_K, min_score: float = config.MIN_SCORE) -> list[Hit]:
    return retrieve_traced(query, k, min_score)[0]


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(f"[{i}] {h.title}\n{h.text}" for i, h in enumerate(hits, 1))


def augment(question: str, hits: list[Hit]) -> str:
    """The text that replaces the user's last message: context + question."""
    if not hits:
        return (
            f"{question}\n\n"
            "(Hinweis: In der Wissensbasis wurde nichts Passendes gefunden. "
            "Wenn es eine Produktfrage ist, sag das offen und erfinde nichts.)"
        )
    return (
        "Kontext aus der Personal-Planer-Wissensbasis:\n\n"
        f"{format_context(hits)}\n\n"
        "---\n"
        f"Frage: {question}\n\n"
        "Beantworte die Frage nur mit dem Kontext oben und zitiere die Quellen als [1], [2]. "
        "Wenn der Kontext die Antwort nicht enthält, sag das."
    )