from functools import lru_cache

from sentence_transformers import CrossEncoder

from app.config import settings


@lru_cache(maxsize=1)
def _model() -> CrossEncoder:
    return CrossEncoder(settings.reranker_model)


def rerank(query: str, candidates: list[str]) -> list[float]:
    if not candidates:
        return []
    pairs = [(query, text) for text in candidates]
    return [float(s) for s in _model().predict(pairs)]
