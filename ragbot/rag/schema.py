from dataclasses import dataclass


@dataclass
class Document:
    source: str      # URL or file path
    title: str
    text: str


@dataclass
class Chunk:
    source: str
    title: str
    text: str
    index: int       # position within its document


@dataclass
class Hit(Chunk):
    score: float     # cosine similarity, 0..1