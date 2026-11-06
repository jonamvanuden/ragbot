import uuid
from dataclasses import asdict
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ragbot import config
from ragbot.rag.schema import Chunk, Hit


@lru_cache
def client() -> QdrantClient:
    config.QDRANT_DIR.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(config.QDRANT_DIR))   # embedded: just a folder, no server


def reset(dim: int):
    c = client()
    if c.collection_exists(config.COLLECTION):
        c.delete_collection(config.COLLECTION)
    c.create_collection(config.COLLECTION,
                        vectors_config=VectorParams(size=dim, distance=Distance.COSINE))


def add(chunks: list[Chunk], vectors: list[list[float]], batch: int = 256):
    points = [
        PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c.source}#{c.index}")),
                    vector=v, payload=asdict(c))
        for c, v in zip(chunks, vectors)
    ]
    for i in range(0, len(points), batch):
        client().upsert(config.COLLECTION, points=points[i:i + batch])


def search(vector: list[float], k: int) -> list[Hit]:
    c = client()
    if not c.collection_exists(config.COLLECTION):
        return []
    res = c.query_points(config.COLLECTION, query=vector, limit=k, with_payload=True)
    return [Hit(score=p.score, **p.payload) for p in res.points]

def close():
    """Close the embedded DB cleanly (avoids the noisy shutdown traceback)."""
    if client.cache_info().currsize:     # only if it was ever opened
        client().close()
        client.cache_clear()