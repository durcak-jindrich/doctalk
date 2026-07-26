"""Structured logging and the per-node timing record.

Two things live here because they answer the same question — "what did this
request actually do?" — from two directions: `NodeStep` is what the user sees
in the answer's observability panel, and the JSON-lines log is what an
operator greps later. Both carry the same `trace_id`, so a support question
about one answer can be tied to its log lines.

Logs are JSON Lines by default so they can be shipped and queried without
parsing prose (Azure Log Analytics ingests them as-is). `LOG_FORMAT=text`
gives readable console output for local work.
"""

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

#: Correlates every log line and answer produced while handling one request.
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)

# Attributes present on every LogRecord; anything else a caller attached via
# `extra=` is application data and belongs in the JSON payload.
_STANDARD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


@dataclass(frozen=True)
class NodeStep:
    """One graph node's execution: how long it took, and what it decided.

    `detail` is node-specific and deliberately open — the retrieval node
    reports chunk counts, `draft` reports tokens, `govern` reports its verdict.
    """

    node: str
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def current_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace(trace_id: str | None = None):
    """Bind a trace id for the duration of a request or script run."""
    token = _trace_id.set(trace_id or new_trace_id())
    try:
        yield _trace_id.get()
    finally:
        _trace_id.reset(token)


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line, with any `extra=` fields merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if (trace_id := current_trace_id()) is not None:
            payload["trace_id"] = trace_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable console output, trace id included when there is one."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = current_trace_id()
        prefix = f"[{trace_id}] " if trace_id else ""
        return (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"{record.name}: {prefix}{record.getMessage()}"
        )


def configure_logging(log_format: str, level: str = "INFO") -> None:
    """Install the root handler. Called once, at application startup."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLinesFormatter() if log_format == "json" else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; let them fall through to ours so the
    # whole process logs in one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


@contextmanager
def timed():
    """Wall-clock milliseconds for a block, readable as `handle.ms`."""

    class _Elapsed:
        ms = 0.0

    handle = _Elapsed()
    started = time.perf_counter()
    try:
        yield handle
    finally:
        handle.ms = (time.perf_counter() - started) * 1000
