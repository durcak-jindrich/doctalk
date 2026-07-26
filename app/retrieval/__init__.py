from .embedder import embed_texts, embedding_dim
from .reranker import rerank
from .retriever import HybridRerankRetriever, RetrievedChunk


def warm_models() -> None:
    """Force the embedding and reranker models to load.

    Both are `lru_cache`d, so this is a one-off cost paid at startup instead
    of inside whichever request happens to be first.
    """
    embed_texts(["warmup"])
    rerank("warmup", ["warmup"])


__all__ = [
    "HybridRerankRetriever",
    "RetrievedChunk",
    "embed_texts",
    "embedding_dim",
    "rerank",
    "warm_models",
]
