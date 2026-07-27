"""FastAPI entrypoint: the API, plus the static frontend it serves."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import ask_router, documents_router
from app.api.auth import verify_token
from app.config import settings
from app.observability import configure_logging, new_trace_id, trace
from app.retrieval import warm_models

configure_logging(settings.log_format, settings.log_level)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the embedding and reranker models before serving traffic.

    Several hundred MB that would otherwise load inside the first question,
    making the demo's first answer look far slower than the system is. The LLM
    client is deliberately *not* built here: it needs an API key, and the app
    must still boot and serve uploads without one.
    """
    logger.info("warming retrieval models...")
    warm_models()
    logger.info("ready")
    yield


app = FastAPI(title="DocTalk", version="0.1.0", lifespan=lifespan)
# `verify_token` is a no-op unless AUTH_ENABLED=true, so this line is inert
# locally and enforced in Azure — one flag, not two code paths. `/health` and
# the static frontend stay unauthenticated: a load balancer probes the
# former, and the latter is just HTML/JS with no data in it.
_protected = [Depends(verify_token)]
app.include_router(documents_router, dependencies=_protected)
app.include_router(ask_router, dependencies=_protected)


@app.middleware("http")
async def bind_trace_id(request: Request, call_next):
    """Tag every log line from one request with the same id.

    Returned as `X-Trace-Id` and echoed in the answer payload, so an answer a
    user is asking about can be tied back to its log lines. An inbound header
    is honoured, which is what lets a future front door correlate across hops.
    """
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    with trace(trace_id):
        response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
