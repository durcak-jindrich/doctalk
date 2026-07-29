"""A real browser against a real server, on a real Postgres.

The server runs in a thread inside the test process, which is what lets one
fixture decide whether `/ask` talks to a fake model or a real one: the graph
is a FastAPI dependency (`app.api.deps.answer_graph`), so it can be
overridden in-process while Chromium drives the same app over HTTP.

Browsers are not a Python dependency — run `uv run playwright install
chromium` once. Without them the suite skips rather than fails, so the
default `uv run pytest` still passes on a machine that has never installed
them.
"""

import socket
import threading
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import urlopen

import psycopg
import pytest
import uvicorn

from app.api.deps import answer_graph
from app.config import settings
from app.graph import build_answer_graph
from app.main import app
from app.retrieval import HybridRerankRetriever
from tests.golden import ObedientClient

#: Model warm-up runs in the lifespan hook before the port answers.
_BOOT_TIMEOUT_S = 120


@dataclass(frozen=True)
class AppServer:
    base_url: str
    #: True when `/ask` is wired to a real provider, so answer *content* can
    #: be asserted. False means a scripted reply: structure only.
    live: bool
    #: How long the browser may wait for an answer to render.
    answer_timeout_ms: float


def _live_answer_budget_ms() -> float:
    """The worst case a single answer is allowed to take, from configuration.

    Derived rather than picked, so re-tuning the provider settings cannot
    leave a stale number here. A free-tier model queues: one observed draft
    took 122s because the SDK burned most of its retry budget, which a flat
    "two minutes" guess would have failed on while the system was working
    correctly.
    """
    per_draft = settings.llm_timeout_seconds * (settings.llm_max_retries + 1)
    return per_draft * settings.synthesis_max_attempts * 1000


@pytest.fixture(autouse=True)
def _require_db():
    # A plain connect, not get_connection(): the latter registers the pgvector
    # adapter, which a database that has never been migrated does not have yet.
    try:
        with psycopg.connect(settings.database_url):
            pass
    except psycopg.OperationalError:
        pytest.skip(
            "Postgres not reachable at DATABASE_URL — start it with `docker compose up -d db`."
        )


@pytest.fixture(scope="session", autouse=True)
def _require_browser(playwright):
    try:
        playwright.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 — any launch failure means "no browser"
        pytest.skip(f"Chromium unavailable ({exc}) — `uv run playwright install chromium`.")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_serving(base_url: str) -> None:
    deadline = time.monotonic() + _BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=2):
                return
        except (URLError, OSError):
            time.sleep(0.25)
    raise RuntimeError(f"server did not come up within {_BOOT_TIMEOUT_S}s")


@pytest.fixture
def app_server(request):
    """The app on a loopback port. Parametrize indirectly with "fake" or "live"."""
    live = getattr(request, "param", "fake") == "live"
    if live and not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set")

    if not live:
        # One client for the whole run: the graph is rebuilt per request by
        # this lambda, but the models behind the retriever are cached.
        fake = ObedientClient()
        app.dependency_overrides[answer_graph] = lambda: build_answer_graph(
            HybridRerankRetriever(), fake
        )

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_serving(base_url)
        # A scripted reply is instant; anything slower is a hang, not a queue.
        yield AppServer(base_url, live, _live_answer_budget_ms() if live else 30_000)
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        app.dependency_overrides.clear()
