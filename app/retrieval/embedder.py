from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


def embedding_dim() -> int:
    """Vector width of the configured embedding model, read from the model itself.

    Derived rather than configured so the `chunks.embedding` column can never
    disagree with what `embed_texts` actually produces.
    """
    dim = _model().get_embedding_dimension()
    if dim is None:
        raise RuntimeError(
            f"{settings.embedding_model!r} does not report an embedding dimension; "
            "it cannot be used as an embedding model."
        )
    return dim


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, normalized for cosine similarity search."""
    if not texts:
        return []
    vectors = _model().encode(list(texts), normalize_embeddings=True)
    return vectors.tolist()
