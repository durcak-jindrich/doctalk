"""FastAPI entrypoint: the API, plus the static frontend it serves."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import ask_router, documents_router
from app.retrieval import warm_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
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
app.include_router(documents_router)
app.include_router(ask_router)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
