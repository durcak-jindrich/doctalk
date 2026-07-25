from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, normalized for cosine similarity search."""
    if not texts:
        return []
    vectors = _model().encode(list(texts), normalize_embeddings=True)
    return vectors.tolist()
