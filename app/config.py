from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres
    database_url: str = "postgresql://doctalk:doctalk@localhost:5432/doctalk"

    # LLM (OpenRouter, OpenAI-SDK-compatible)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "google/gemma-4-31b-it:free"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 800
    llm_timeout_seconds: float = 60.0
    # Retries for transient provider failures (429/5xx), with the SDK's
    # exponential backoff. Free-tier models are rate-limited upstream, so the
    # default sits above the SDK's own default of 2.
    llm_max_retries: int = 4

    # Retrieval. Vector width is not configured here — it is read from the
    # loaded model by `app.retrieval.embedding_dim()`.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    retrieval_top_k: int = 5

    # Synthesis & citation governance.
    # `min_rerank_score` is the cross-encoder logit below which the best
    # retrieved chunk counts as "nothing relevant" and DocTalk refuses instead
    # of answering. Provisional — re-tuned against the Phase 9 eval set.
    min_rerank_score: float = -5.0
    synthesis_max_attempts: int = 2

    # Summarize tool: total chunks fed to a whole-workspace summary, split
    # evenly across the uploaded documents so every one is represented.
    summary_max_chunks: int = 12

    # Workspace
    max_documents: int = 5

    # Azure / auth (feature-flagged off locally)
    auth_enabled: bool = False


settings = Settings()
