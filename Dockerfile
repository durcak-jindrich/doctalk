# Builder stage: build-essential and uv are only needed to resolve/compile
# the venv. Neither ships in the runtime image — smaller image, smaller
# attack surface.
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.18 /uv /usr/local/bin/uv

WORKDIR /srv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Bake the embedder and the reranker into the image. Otherwise every container
# downloads ~180 MB from huggingface.co inside the lifespan hook before it can
# serve, so a cold start costs both time and network egress the app does not
# otherwise need. `config.py` is copied on its own, ahead of the rest of the
# source, so editing application code does not invalidate this layer and
# re-download the weights on every rebuild.
ENV HF_HOME=/opt/cache/huggingface \
    TIKTOKEN_CACHE_DIR=/opt/cache/tiktoken \
    PYTHONDONTWRITEBYTECODE=1
COPY app/config.py ./app/config.py
RUN /srv/.venv/bin/python -c "\
from sentence_transformers import CrossEncoder, SentenceTransformer; \
from app.config import settings; \
SentenceTransformer(settings.embedding_model); \
CrossEncoder(settings.reranker_model)"

COPY app ./app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

# Same idea for tiktoken's BPE table, which it otherwise fetches on the first
# upload. Cheap enough (~2 MB) to sit after the source copy rather than earn
# its own cached layer.
RUN /srv/.venv/bin/python -c "\
import tiktoken; \
from app.chunking.chunker import _ENCODING; \
tiktoken.get_encoding(_ENCODING)"

# Runtime stage: just the venv and source, on the same slim base.
FROM python:3.12-slim AS runtime

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /srv
COPY --from=builder /srv /srv
# Owned by appuser, unlike the venv: an `EMBEDDING_MODEL` pointing at a model
# that was not baked in must still be able to download itself at startup.
COPY --from=builder --chown=appuser:appuser /opt/cache /opt/cache
USER appuser

# `HF_HUB_OFFLINE=1` makes startup use the baked weights and nothing else. It
# is not just a saving: sentence-transformers probes the Hub for optional
# files that do not exist in either repo, so an air-gapped or egress-filtered
# container would burn its retry budget and then fail to boot. Unset it to run
# a model that was not baked in.
ENV PATH="/srv/.venv/bin:${PATH}" \
    HF_HOME=/opt/cache/huggingface \
    HF_HUB_OFFLINE=1 \
    TIKTOKEN_CACHE_DIR=/opt/cache/tiktoken \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# No curl/wget in the runtime image — a stdlib request keeps the healthcheck
# free of an extra installed package.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
