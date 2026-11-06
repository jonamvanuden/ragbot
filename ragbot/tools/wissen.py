"""The knowledge base as a tool, so the agent decides itself when to search the documents."""
from pydantic import BaseModel, Field

from ragbot.tools.registry import Context, tool


class WissenSuchenParams(BaseModel):
    frage: str = Field(min_length=3, description="Suchanfrage in ganzen Worten, z. B. 'Preise Urlaubsplaner'")


@tool(WissenSuchenParams, product="Wissensbasis", kind="read")
def wissen_suchen(p: WissenSuchenParams, ctx: Context) -> dict:
    """Durchsucht die Personal-Planer-Wissensbasis (Website, Preisliste, AGB, Handbücher)
    nach Informationen zu Produkten, Preisen, Verträgen und Funktionen."""
    from ragbot.rag.retriever import retrieve        # lazy: loads the embedding model only when used
    hits = retrieve(p.frage, k=4)
    return {"anzahl": len(hits),
            "treffer": [{"nr": i, "titel": h.title, "quelle": h.source, "score": round(h.score, 3),
                         "text": h.text[:900]} for i, h in enumerate(hits, 1)]}
