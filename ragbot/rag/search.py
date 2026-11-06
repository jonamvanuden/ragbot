"""Test retrieval without the LLM.

    python -m ragbot.rag.search "Wie funktioniert der Urlaubsplaner?"
"""
import sys

from ragbot.rag.retriever import retrieve
from ragbot.rag import store


def main():
    query = " ".join(sys.argv[1:]) or "Was kann der Urlaubsplaner?"
    for h in retrieve(query, min_score=0):
        preview = h.text[:220].replace("\n", " ")
        print(f"{h.score:.3f}  {h.title}  ({h.source})\n       {preview}…\n")
    store.close()


if __name__ == "__main__":
    main()