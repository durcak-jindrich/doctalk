# DocTalk – Discuss Your Documents

## What this is
An interview case study. Build a lightweight system where a user
uploads 1–5 internal documents (PDF/DOCX/MD), asks questions about them, and
gets answers grounded *only* in that content, with citations back to the
source (chunk ID or document name).

Full original brief: `docs/assignment.md` (condensed from `docs/assignment.pdf`).
This file is the working summary — if anything here conflicts with
`docs/assignment.md`, the brief wins.

## Baseline requirements — non-negotiable, must all work
- Accept 1–5 uploaded documents (PDF/DOCX/MD)
- Simple Q&A interface — minimal, fully functional UI for the backend access that meets all frontend requirements
- Answers must come **only** from uploaded content — never fall back to
  general knowledge, never guess
- Every answer cites its source (chunk ID or document name)
- README documents architecture and assumptions
- Runs locally end to end

## Further options — after Baseline works 
1. Agentic upgrade — LangGraph orchestrating retrieval / summarization / governance as tools
2. RAG upgrade — embeddings + vector store (FAISS)
3. Azure-ready deployment — FastAPI + AAD auth + Key Vault, ready for App Service / Container Apps
4. Observability — latency/cost metrics + evaluation hooks

Implement further option items top-down after baseline is solid.

## Presentation — not part of the code submission
Delivered live, on the call, also to a non-technical Product Owners. Focus on:
accessible explanation, ability to go deeper on technical detail on
request, formatting/clarity. Build this last, after the system
works — see "Session workflow" below.

## Deliverables
- Source code + quickstart/run instructions
- Demo script: upload → ask → show citations
- Also include: architecture/security/limitations write-up, evaluation report
  (groundedness, latency), governance checklist draft

## Tech stack

- **Backend:** Python 3.12 (pinned — `sentence-transformers` has no 3.14
  wheel yet), FastAPI, Uvicorn, LangGraph
- **Parsing:** `pdfplumber` (PDF), `python-docx` (DOCX), plain read (MD)
- **Chunking:** structure-aware per format; `tiktoken` for token-size
  measurement
- **Storage/retrieval:** Postgres + `pgvector` + `pg_search` (ParadeDB
  locally; `ts_rank` fallback on Azure), RRF fusion,
  `sentence-transformers` CrossEncoder reranker
- **LLM:** `LLMClient` interface → `OpenRouterClient`, model configurable
  via env var
- **Orchestration:** LangGraph graph (route → retrieve | summarize-tool →
  draft → govern) as the production `/ask` pipeline
- **Frontend:** static HTML/CSS/vanilla JS served by FastAPI, with an
  observability panel per answer
- **Containerization:** Docker Compose locally (`app` + `paradedb/paradedb`);
  Dockerfile + Bicep IaC for Azure Container Apps
- **Testing:** `pytest`; fake `LLMClient` for the fast suite. Live-LLM tests
  are marked `live` and deselected by default — `uv run pytest` never spends
  OpenRouter quota
- **Lint/format:** `ruff`
- **Config/secrets:** `.env` + `.env.example`, `pydantic-settings`; Key
  Vault + Managed Identity in Azure

Full reasoning behind each choice: `docs/technical-decisions.md`. Build
order and phase scope: `PLAN.md`.

## Commands

- **Install deps:** `uv sync`
- **Run locally (API only, needs Postgres reachable):**
  `uv run uvicorn app.main:app --reload`
- **Run full stack (app + ParadeDB Postgres, via Docker):**
  `docker compose up --build`
- **Test:** `uv run pytest`
- **Lint:** `uv run ruff check .`
- **Format:** `uv run ruff format .`

## Architecture

- `app/main.py` — FastAPI entrypoint: routers, static frontend, model warmup
- `app/config.py` — `pydantic-settings` config, loaded from `.env`; overlays
  secrets from Azure Key Vault via Managed Identity when
  `AZURE_KEY_VAULT_URL` is set
- `app/observability.py` — JSON-lines logging, trace ids, `NodeStep` records
- `app/api/` — HTTP routes (upload, ask, document list/view/delete), wire
  schemas, request dependencies, and Entra ID (AAD) bearer-token validation
  (`auth.py`, feature-flagged by `AUTH_ENABLED`)
- `app/static/` — frontend: HTML/CSS/vanilla JS, no build step
- `app/parsers/` — PDF/DOCX/MD → structured text extraction
- `app/chunking/` — structure-aware chunking + chunk ID scheme
- `app/storage/` — Postgres/`pgvector` persistence layer, workspace cap
  enforcement
- `app/retrieval/` — `HybridRerankRetriever`: dense (`pgvector`) + lexical
  (`pg_search`) search, RRF fusion, cross-encoder rerank
- `app/llm/` — `LLMClient` interface + `OpenRouterClient` adapter
- `app/synthesis/` — grounding prompts, deterministic citation validator,
  refusal vocabulary (primitives only; the graph owns the control flow)
- `app/graph/` — the `/ask` pipeline: route → retrieve or summarize-tool →
  draft → govern, with the corrective retry as a real edge.
  `answer_question(conn, question)` is the single entry point
- `migrations/` — versioned schema SQL + `apply.sh`, the psql runner the
  one-shot `migrate` Compose service executes before the backend starts
- `scripts/reset_db.py` — drop and replay all migrations (local dev only)
- `scripts/manual_smoke_test*.py` — manual end-to-end walkthroughs used to
  verify each phase (ingestion/retrieval, then grounded answering)
- `scripts/evaluate.py` — Phase 9 evaluation over the golden set, writes
  `docs/evaluation.md`
- `tests/unit/`, `tests/integration/` — fast fake-LLM suite + opt-in
  `live`-marked tests; `tests/golden.py` holds the fixture workspace and
  golden Q&A set, shared with `scripts/evaluate.py`
- `docs/` — `assignment.md` (brief), `technical-decisions.md` (why),
  `azure-deployment.md`, `evaluation.md`, `security-limitations.md`,
  `governance-checklist.md`
- `infra/` — Bicep IaC: Container Apps, Postgres, Key Vault, managed
  identity + RBAC, ACR, Log Analytics (`docs/azure-deployment.md`)
- `.github/workflows/` — example CI (lint/test/Bicep check) and manual
  deploy workflows
- `PLAN.md` — phased implementation plan (source of truth for build order)
- `docker-compose.yml` / `Dockerfile` — local and containerized run paths

As of now Phases 0–10 are built: the baseline runs locally end to end —
upload, ask, cited answers, in the browser — with per-node timings, structured
logs, unit/integration/live test coverage, an Azure deployment path (AAD
auth, Key Vault, Bicep IaC) that is written and CI-validated but not deployed
against a live subscription, a golden-set evaluation report
(`docs/evaluation.md`), and consolidated security/governance documentation
(`docs/security-limitations.md`, `docs/governance-checklist.md`). Phase 11
(presentation prep) is what's left. See `README.md`'s status table and
`PLAN.md` for phase status.

## Rules
- After each phase, explicitly test the output, sanity-check assumptions,
  and self-review against the evaluation criteria in CLAUDE.md before you proceed. 
- Never fabricate a citation. If nothing relevant was retrieved, say so
  explicitly instead of answering anyway.
- Treat groundedness as the core requirement, not a stretch feature — it's
  worth more to get this right than to add RAG/agents on a shaky base.
- Keep secrets (AAD, Key Vault, API keys) out of source control — `.env`
  only, and `.env` is gitignored.
- **OpenRouter calls cost quota — spend them deliberately.** The default
  test/dev loop must make zero LLM calls: use the fake `LLMClient`. Live
  calls only behind an explicit opt-in (`pytest -m live`, `--live` on a smoke
  script) and only when a real model's behaviour is the thing being checked.
  Never poll or retry a live model to explore behaviour.
- Any non-obvious design decision goes in the README, not just in chat —
  the interview will reference the README
- Follow the best software engineering an UI/UX practices (coding, design, logging, testing, layouts, clean code, readable, maintainable project)

## Documentation

**Concise is a hard requirement, not a style preference** — bloated docs go
unread, and an unread README fails the brief. Markdown must be complete and
fully informational *and* short enough to read end to end.

- One entry per decision: choice, why, trade-off — 1–3 lines. Not a
  narrative, not a record of how you got there.
- Record what shapes the system or what a reader would question. Routine
  implementation detail and code-level gotchas belong in a code comment.
- Edit existing entries rather than appending per-phase sections —
  `docs/technical-decisions.md` must never become an append-only log.
- Say it once: `README.md` gets the headline and a link,
  `docs/technical-decisions.md` holds the reasoning.
- Deleting stale or redundant prose is part of finishing a phase.

These docs are the running decisions log for the final presentation: it must
stay possible to draft problem → approach → key decisions → trade-offs →
results from them.
