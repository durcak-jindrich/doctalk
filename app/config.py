import logging
import os
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Key Vault secret name -> environment variable it overlays. Only the two
# genuinely secret values — everything else (tenant/client IDs, feature
# flags, model names) is ordinary Container App configuration, set directly
# by Bicep, not worth a Key Vault round-trip.
_KEY_VAULT_SECRET_ENV_MAP = {
    "database-url": "DATABASE_URL",
    "openrouter-api-key": "OPENROUTER_API_KEY",
}


def _load_secrets_from_key_vault() -> None:
    """Overlay Key Vault secrets onto the environment before `Settings` reads it.

    No-op unless `AZURE_KEY_VAULT_URL` is set, so a local `.env` run never
    imports `azure-identity` or touches the network. An environment variable
    already set (e.g. a Container App app setting) wins over Key Vault, which
    wins over anything in `.env` — `os.environ` here runs before
    `Settings()`, and pydantic-settings prefers `os.environ` over the file.
    """
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if not vault_url:
        return

    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    # `managed_identity_client_id` is passed explicitly rather than left for
    # `DefaultAzureCredential` to infer from the environment: this app already
    # uses `AZURE_CLIENT_ID` for the Entra ID app registration that AAD tokens
    # are validated against (see app/api/auth.py), which is a *different*
    # identity from the user-assigned managed identity used here. Reusing the
    # env var for both would make `DefaultAzureCredential` try to authenticate
    # as the wrong identity.
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_MANAGED_IDENTITY_CLIENT_ID")
    )
    client = SecretClient(vault_url=vault_url, credential=credential)
    for secret_name, env_var in _KEY_VAULT_SECRET_ENV_MAP.items():
        if os.environ.get(env_var):
            continue
        try:
            os.environ[env_var] = client.get_secret(secret_name).value
        except Exception:
            logger.exception("could not load secret %r from Key Vault", secret_name)


_load_secrets_from_key_vault()


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
    # Per-file upload ceiling. Parsing and embedding load the whole file into
    # memory, so this is a memory bound, not just a politeness limit.
    max_upload_bytes: int = 10 * 1024 * 1024

    # Observability. JSON Lines by default so logs are queryable as shipped;
    # `text` is the readable console format for local work.
    log_format: Literal["json", "text"] = "json"
    log_level: str = "INFO"

    # Azure AAD auth (feature-flagged off locally, on in Azure). Tenant/client
    # ID are the Entra ID app registration guarding the API — see
    # docs/azure-deployment.md.
    auth_enabled: bool = False
    azure_tenant_id: str = ""
    azure_client_id: str = ""

    @model_validator(mode="after")
    def _auth_needs_tenant_and_client(self) -> "Settings":
        if self.auth_enabled and not (self.azure_tenant_id and self.azure_client_id):
            raise ValueError(
                "AUTH_ENABLED=true requires AZURE_TENANT_ID and AZURE_CLIENT_ID "
                "(the Entra ID app registration to validate tokens against)."
            )
        return self


settings = Settings()
