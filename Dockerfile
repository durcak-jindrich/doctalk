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

COPY app ./app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

# Runtime stage: just the venv and source, on the same slim base.
FROM python:3.12-slim AS runtime

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /srv
COPY --from=builder /srv /srv
USER appuser

ENV PATH="/srv/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# No curl/wget in the runtime image — a stdlib request keeps the healthcheck
# free of an extra installed package.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
