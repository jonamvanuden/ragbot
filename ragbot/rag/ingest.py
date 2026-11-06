"""Fill the knowledge base.

    python -m ragbot.rag.ingest              # website + data/docs
    python -m ragbot.rag.ingest --no-web     # only data/docs
"""
import argparse
import re

from ragbot import config
from ragbot.rag import store
from ragbot.rag.chunker import split
from ragbot.rag.embedder import embed
from ragbot.rag.loaders import crawl_site, load_files
from ragbot.rag.schema import Document

# Matches the version in names like "Preisliste_V5.8.pdf" or "AGB-V2.6F.pdf"
VERSION = re.compile(r"[_-]V(\d+(?:\.\d+)*)[A-Za-z]?(?=\.\w+$)", re.I)


def latest_versions(docs: list[Document]) -> list[Document]:
    """If a document exists in several versions, keep only the newest one."""
    best: dict[str, tuple[tuple[int, ...], Document]] = {}
    for d in docs:
        m = VERSION.search(d.source)
        if not m:                                   # no version in the name -> always keep
            best[d.source] = ((), d)
            continue
        family = VERSION.sub("", d.source)          # name without version = document family
        version = tuple(int(x) for x in m.group(1).split("."))
        if family in best:
            old_version, old_doc = best[family]
            if version > old_version:
                print(f"  - veraltet: {old_doc.title}  →  ersetzt durch V{m.group(1)}")
                best[family] = (version, d)
            else:
                print(f"  - veraltet: {d.title}")
        else:
            best[family] = (version, d)
    return [d for _, d in best.values()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--max-pages", type=int, default=config.MAX_PAGES)
    args = ap.parse_args()

    docs = []
    if not args.no_web:
        print(f"Crawle {config.START_URL} …")
        for d in crawl_site(max_pages=args.max_pages):
            print(f"  + {d.title[:60]:60} {d.source}")
            docs.append(d)
    if config.DOCS_DIR.exists():
        for d in load_files():
            print(f"  + {d.title[:60]:60} {d.source}")
            docs.append(d)

    print("\nVersionen prüfen …")
    docs = latest_versions(docs)                     # NEW: drop outdated versions

    # Chunk + drop exact duplicates (same block repeated on many pages)
    chunks, seen = [], set()
    for d in docs:
        for c in split(d):
            if c.text not in seen:
                seen.add(c.text)
                chunks.append(c)

    print(f"\n{len(docs)} Dokumente → {len(chunks)} Chunks")
    if not chunks:
        print("Nichts zu indexieren.")
        return

    vectors = embed([f"{c.title}\n{c.text}" for c in chunks])   # title gives each chunk context
    store.reset(len(vectors[0]))
    store.add(chunks, vectors)
    print(f"Fertig. Gespeichert in {config.QDRANT_DIR}")
    store.close()


if __name__ == "__main__":
    main()