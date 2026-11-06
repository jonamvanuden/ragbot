from functools import lru_cache

from sentence_transformers import SentenceTransformer

from ragbot import config


@lru_cache
def _model() -> SentenceTransformer:
    m = SentenceTransformer(config.EMBED_MODEL, device=config.EMBED_DEVICE)
    if config.EMBED_DEVICE == "cuda":
        m.half()                 # fp16: ~1.2 GB VRAM instead of ~2.3 GB
    return m


def embed(texts: list[str]) -> list[list[float]]:
    return _model().encode(
        texts, normalize_embeddings=True, batch_size=16,
        show_progress_bar=len(texts) > 50,
    ).tolist()