# DocTalk — Discuss Your Documents

Grounded Q&A over a small set of internal documents. Upload 1–5 files
(PDF/DOCX/MD), ask questions, and get answers sourced **only** from that
content, with citations back to the originating chunk/document. If nothing
relevant is found, DocTalk says so instead of guessing.

> **Status: early build (Phase 0 of `PLAN.md`).** Repo scaffold, config, and
> Docker Compose setup exist; the ingestion/retrieval/synthesis pipeline
> described below is the target architecture, not yet functional. This
> section and the rest of the README are updated at the end of every build
> phase — see [Project status](#project-status) for exactly what runs today.

## Contents
- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Quickstart](#quickstart)
- [Repository layout](#repository-layout)
- [Key design decisions & assumptions](#key-design-decisions--assumptions)
- [Limitations](#limitations)
- [Project status](#project-status)
- [Further reading](#further-reading)

## What it does

1. Upload up to 5 documents (PDF, DOCX, or Markdown) into one working
   document set.
2. Ask a question in natural language.
3. Get an answer grounded strictly in the uploaded content, with each claim
   citing the source chunk/document — or an explicit refusal if the answer
   isn't in the documents.

This is built as an interview case study; the full assignment brief is in
[`docs/assignment.md`](docs/assignment.md).

## Architecture

Target pipeline (see [Project status](#project-status) for what's live):

```
Upload (PDF/DOCX/MD)
   → Parse (pdfplumber / python-docx / plain read)
   → Structure-aware chunk (+ chunk ID assignment)
   → Store (Postgres: documents, chunks, pgvector embeddings)

Ask
   → LangGraph pipeline:
       retrieve  (dense pgvector + lexical pg_search, RRF-fused,
                  cross-encoder reranked)
       → synthesize (LLM answers strictly from retrieved chunks)
       → governance (validates every citation resolves to a real,
                      retrieved chunk; refuses if retrieval was empty)
   → Answer + citations (+ observability metadata: latency, tokens, cost)
```

Everything downstream of "Ask" runs through one LangGraph graph — there is
no separate simple/agentic mode; retrieval, synthesis, and citation
governance are graph nodes from the start.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, LangGraph |
| Parsing | `pdfplumber` (PDF), `python-docx` (DOCX), plain read (MD) |
| Chunking | Structure-aware per format, `tiktoken` for size measurement |
| Storage | Postgres + `pgvector` (dense) + `pg_search`/ParadeDB (lexical) |
| Retrieval | RRF fusion of dense + lexical, `sentence-transformers` CrossEncoder rerank |
| LLM | `LLMClient` interface → OpenRouter (model set via env var) |
| Orchestration | LangGraph (retrieve → synthesize → governance, + summarize tool) |
| Frontend | Static HTML/CSS/vanilla JS served by FastAPI |
| Containerization | Docker Compose locally; Dockerfile + Bicep for Azure Container Apps |
| Testing | `pytest`, fake-LLM fast suite + one live OpenRouter e2e test |
| Config/secrets | `.env` (gitignored) via `pydantic-settings`; Key Vault + Managed Identity on Azure |

Rationale for each choice — alternatives considered, trade-offs accepted —
is in [`docs/technical-decisions.md`](docs/technical-decisions.md), not
duplicated here.

## Quickstart

**Requirements:** Docker (recommended path), or Python 3.12 + [`uv`](https://docs.astral.sh/uv/) for local dev.

```bash
cp .env.example .env
# fill in OPENROUTER_API_KEY (free tier works — see .env.example for a free model default)
```

**Run everything (app + Postgres/ParadeDB) via Docker:**
```bash
docker compose up --build
# API at http://localhost:8000, health check at /health
```

**Run the API locally against a Postgres you manage yourself:**
```bash
uv sync
uv run uvicorn app.main:app --reload
```

**Tests / lint:**
```bash
uv run pytest
uv run ruff check .
```

*(Today, only `/health` exists — there's no upload/ask flow to exercise
yet. This section will gain the actual demo script — upload → ask →
citations — once Phase 5 lands.)*

## Repository layout

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI entrypoint |
| `app/config.py` | Settings (`pydantic-settings`, loaded from `.env`) |
| `app/api/` | HTTP routes: upload, ask, document list/view/delete |
| `app/parsers/` | PDF/DOCX/MD → structured text |
| `app/chunking/` | Structure-aware chunking + chunk ID scheme |
| `app/storage/` | Postgres/`pgvector` persistence, workspace cap enforcement |
| `app/retrieval/` | Hybrid dense+lexical retriever, RRF fusion, reranking |
| `app/llm/` | `LLMClient` interface + OpenRouter adapter |
| `app/graph/` | LangGraph graph: retrieve → synthesize → governance |
| `tests/unit/`, `tests/integration/` | Fake-LLM fast suite + live e2e test |
| `docs/` | Brief, technical decisions, and (later) security/governance/eval docs |
| `PLAN.md` | Phased build plan — source of truth for what's done vs pending |

## Key design decisions & assumptions

Full detail and alternatives-considered in
[`docs/technical-decisions.md`](docs/technical-decisions.md); the
headlines:

- **Document workspace:** one bounded set, hard-capped at 5 documents
  (matches the brief's "1–5 documents") — not a growing multi-session
  corpus. Uploads past the cap are rejected until a document is removed.
- **Retrieval:** hybrid dense (`pgvector`) + lexical (`pg_search`/BM25),
  RRF-fused, then cross-encoder reranked — chosen over lexical-only (misses
  paraphrases) or dense-only (weak on exact IDs/names/numbers).
- **Groundedness is non-negotiable:** the LLM answers only from retrieved
  chunks; a deterministic governance step validates every citation against
  what was actually retrieved and refuses rather than fabricates when
  nothing relevant is found.
- **LLM provider:** OpenRouter for local dev/demo, behind an `LLMClient`
  interface; direct Azure OpenAI documented as the production alternative.
- **Runtime pinned to Python 3.12**, not the system's 3.14, because
  `sentence-transformers` has no 3.14 wheel yet.

## Limitations

*(Will expand with retrieval/synthesis-specific limitations as those
phases land — e.g. reranker context-window limits, OCR/scanned-PDF
handling, multi-lingual support.)*

- Nothing beyond a scaffold is implemented yet — see
  [Project status](#project-status).
- Single bounded 5-document workspace by design, not a document library
  (see above) — not a limitation to "fix," a scope decision.
- OpenRouter free-tier models are rate-limited and lower-quality than
  paid alternatives; acceptable for a demo, flagged as a production
  swap-out.

## Project status

Tracking [`PLAN.md`](PLAN.md)'s phases. Current: **Phase 0 — Setup &
infrastructure** (in progress).

| Phase | Status |
|---|---|
| 0 — Setup & infrastructure | In progress |
| 1 — Ingestion & storage | Not started |
| 2 — Hybrid retrieval + reranking | Not started |
| 3 — Grounded synthesis & citation governance | Not started |
| 4 — Agentic orchestration (LangGraph) | Not started |
| 5 — API + frontend | Not started |
| 6 — Observability instrumentation | Not started |
| 7 — Testing | Not started |
| 8 — Azure readiness | Not started |
| 9 — Evaluation | Not started |
| 10 — Documentation (README finalization) | Not started |
| 11 — Presentation prep | Not started |

## Further reading

- [`docs/assignment.md`](docs/assignment.md) — original brief
- [`docs/technical-decisions.md`](docs/technical-decisions.md) — full
  rationale behind every architectural choice
- [`PLAN.md`](PLAN.md) — phased build plan
- [`CLAUDE.md`](CLAUDE.md) — working project instructions/conventions
