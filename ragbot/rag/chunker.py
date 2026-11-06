import re

from ragbot import config
from ragbot.rag.schema import Chunk, Document


def split(doc: Document, size: int = config.CHUNK_SIZE,
          overlap: int = config.CHUNK_OVERLAP) -> list[Chunk]:
    """Pack paragraphs into ~size-char chunks; carry `overlap` chars into the next one."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", doc.text) if p.strip()]
    pieces = [p[i:i + size] for p in paras for i in range(0, len(p), size)]  # cut giant paragraphs

    out, buf = [], ""
    for p in pieces:
        if buf and len(buf) + len(p) + 2 > size:
            out.append(buf)
            buf = buf[-overlap:]
        buf = f"{buf}\n\n{p}".strip()
    if buf:
        out.append(buf)
    return [Chunk(doc.source, doc.title, text, i) for i, text in enumerate(out)]