# DocTalk — Implementation Plan

## Context

DocTalk: a user uploads 1–5 internal documents (PDF/DOCX/MD), asks questions,
and gets answers grounded *only* in that content, with citations. The repo
currently has only `CLAUDE.md` and `docs/assignment.md` — no code yet.

**Scope:** RAG, LangGraph orchestration, Azure-readiness, and observability 
are treated as required deliverables here, not optional extras. Phase order
below follows build dependencies (retrieval before orchestration, orchestration
before observability).

This file covers what to build and in what order. The reasoning behind each
architectural choice below — alternatives weighed, why they were rejected — 
lives in `docs/technical-decisions.md`.

## Document workspace scope
Single bounded workspace, hard-capped at 5 documents, matching the brief's
"1–5 documents." Uploads past the cap are rejected with a clear message
until a document is explicitly removed — no silent eviction, no
multi-session document library. Postgres persistence keeps this one working
set alive across restarts; it isn't there to accumulate documents
indefinitely. Rationale in `docs/technical-decisions.md`.

## README: baseline deliverable
README documents architecture and assumptions under
**baseline requirements — non-negotiable**. It
must not be scheduled behind the optional stretch phases (RAG internals,
LangGraph, Azure, observability). Concretely:

- A first working draft is written at the end of Phase 5 (API + frontend
  in place, so the system genuinely "runs locally end to end" per
  CLAUDE.md's baseline bar), covering what exists at that point:
  setup/run, baseline architecture, assumptions, known limitations.
- It is updated at the end of every phase after that — not left untouched
  until the end — so it never drifts from what the code actually does.
- Phase 10 is a *finalization* pass (final architecture diagram, full demo
  script, consolidated assumptions/limitations), not the README's first
  draft.

## Tech stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, LangGraph
- **Parsing:** `pdfplumber` (PDF, page-aware), `python-docx` (DOCX,
  style-aware), plain read (MD)
- **Chunking:** structure-aware per format; `tiktoken` for token-size
  measurement
- **Storage/retrieval:** Postgres + `pgvector` + `pg_search` (ParadeDB
  locally; `ts_rank` fallback on Azure), RRF fusion, `sentence-transformers`
  CrossEncoder reranker
- **LLM:** `LLMClient` interface → `OpenRouterClient`, model configurable
  via env var
- **Orchestration:** LangGraph graph (retrieve → synthesize → governance,
  plus a summarize tool) as the production `/ask` pipeline
- **Frontend:** static HTML/CSS/vanilla JS served by FastAPI, with an
  observability panel per answer
- **Containerization:** Docker Compose locally (`app` + `paradedb/paradedb`);
  Dockerfile + Bicep IaC for Azure Container Apps
- **Testing:** `pytest`; fake `LLMClient` for the fast suite, one real
  end-to-end test against a live OpenRouter call (structural assertions
  only, skips cleanly with no API key)
- **Lint/format:** `ruff`
- **Config/secrets:** `.env` + `.env.example`; `pydantic-settings`; Key
  Vault + Managed Identity in Azure

## Phased plan

Ordering reflects build dependencies only — every phase is required.

**Phase 0 — Setup & infrastructure**
Repo scaffold (`app/`, `tests/`), `pyproject.toml`, Docker Compose
(`app` + `paradedb/paradedb`, named volume), `.env.example`, `.gitignore`.
Fill in `CLAUDE.md`'s Tech Stack / Commands / Architecture TODOs. Initial
commit.

**Phase 1 — Ingestion & storage**
Parsers (PDF/DOCX/MD) → structure-aware chunker → document/chunk ID scheme →
persistence layer writing documents, chunks, embeddings into Postgres,
enforcing the 5-document workspace cap.

**Phase 2 — Hybrid retrieval + reranking**
Dense (`pgvector`) search, lexical (`pg_search`/BM25) search, RRF fusion,
cross-encoder rerank — built and unit-tested as a standalone, directly
callable `Retriever`.

**Phase 3 — Grounded synthesis & citation governance**
`LLMClient`/OpenRouter adapter, strict grounding prompt, citation parsing,
deterministic citation validator, explicit refusal path when nothing
relevant is retrieved.

**Phase 4 — Agentic orchestration (LangGraph)**
Build the LangGraph graph (retrieve/synthesize/governance/summarize) as the
production `/ask` entry point.

**Phase 5 — API + frontend**
FastAPI endpoints (upload, ask, list/view/delete documents); polished
frontend — drag/drop upload with remaining-slot indicator, chat-style Q&A
with citation chips, observability panel. Baseline now runs locally
end to end — write the first `README.md` draft (setup/run, baseline
architecture, assumptions, limitations) before moving on to Phase 6.

**Phase 6 — Observability instrumentation**
Timing + token/usage capture around each graph node, structured
(JSON-lines) logs, wired into both the frontend panel and the eval script.

**Phase 7 — Testing**
Unit tests (parsers, chunker, retrieval legs + fusion + rerank, citation
validator, fake-LLM synthesis). Integration test: fixture doc set + golden
Q&A set (including a deliberately unanswerable question) against the
fake-LLM suite. One real end-to-end test drives the actual graph through a
live OpenRouter call.

**Phase 8 — Azure readiness**
Dockerfile hardening, Bicep IaC, Key Vault/Managed Identity wiring, AAD auth
middleware, `docs/azure-deployment.md`, example GitHub Actions workflow.

**Phase 9 — Evaluation**
Eval script over the golden Q&A set: citation validity rate, faithfulness
rate, latency breakdown, retrieval-leg contribution, cost summary. Produces
`docs/evaluation.md`.

**Phase 10 — Documentation**
Finalize `README.md` (first drafted at the end of Phase 5) with the full
architecture, demo script, and consolidated assumptions/limitations across
all phases; `docs/security-limitations.md`, `docs/governance-checklist.md`.
Finalize `CLAUDE.md`.

**Phase 11 — Presentation prep**
Non-technical narrative (problem → live demo → why answers are trustworthy)
plus a technical appendix for follow-up questions. Rehearse against the
demo script.

**Version control:** commit per logical unit (scaffold → ingestion →
retrieval → synthesis/governance → agent orchestration → API/frontend →
observability → tests → Azure readiness → eval → docs), not one large
commit.

## Assumptions / open questions to flag to the Product Owner

- **Persistence:** Postgres + pgvector persists across restarts by design
  (Docker volume locally, managed instance in Azure).
- **Azure `pg_search` availability:** assumed to need a fallback to native
  `ts_rank` on Azure Database for PostgreSQL — confirm against current
  Azure docs before presenting as settled.
- **Azure deployment:** IaC is written and validated (`az deployment group
  validate`) but not assumed to be deployed against a live subscription
- **Auth:** AAD auth is implemented but feature-flagged off locally, on in
  Azure. 
- **OpenRouter:** accepted for this build; direct Azure OpenAI documented
  as the production alternative.
- **Real end-to-end test cost:** the one live OpenRouter call per run is
  kept to a cheap/free model on a small fixture — flag if CI should skip
  it instead.

## Verification

- `pytest`: fast suite (fake `LLMClient`) plus the one real end-to-end
  test — both drive the LangGraph graph, not just individual functions.
- `docker compose up` as the local run path, exercised manually through
  the demo script (upload → ask → citations → observability panel) before
  calling it done.
- `az deployment group validate` against the Bicep templates as an Azure
  IaC sanity check.
- Eval script output (Phase 9) reviewed for citation validity rate,
  latency breakdown, and retrieval-leg contribution before presentation
  prep.
