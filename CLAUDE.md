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
- **Orchestration:** LangGraph graph (retrieve → synthesize → governance,
  plus a summarize tool) as the production `/ask` pipeline
- **Frontend:** static HTML/CSS/vanilla JS served by FastAPI, with an
  observability panel per answer
- **Containerization:** Docker Compose locally (`app` + `paradedb/paradedb`);
  Dockerfile + Bicep IaC for Azure Container Apps
- **Testing:** `pytest`; fake `LLMClient` for the fast suite, one real
  end-to-end test against a live OpenRouter call
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

- `app/main.py` — FastAPI app entrypoint (currently just `/health`)
- `app/config.py` — `pydantic-settings` config, loaded from `.env`
- `app/api/` — HTTP routes: upload, ask, document list/view/delete
- `app/parsers/` — PDF/DOCX/MD → structured text extraction
- `app/chunking/` — structure-aware chunking + chunk ID scheme
- `app/storage/` — Postgres/`pgvector` persistence layer, workspace cap
  enforcement
- `app/retrieval/` — `HybridRerankRetriever`: dense (`pgvector`) + lexical
  (`pg_search`) search, RRF fusion, cross-encoder rerank
- `app/llm/` — `LLMClient` interface + `OpenRouterClient` adapter
- `app/graph/` — LangGraph graph wiring retrieve → synthesize →
  governance (+ summarize tool) into the `/ask` pipeline
- `scripts/init_db.py` — one-shot DB schema bootstrap (extensions, tables,
  indexes), run once before the backend starts, not created lazily by
  application code
- `tests/unit/`, `tests/integration/` — fast fake-LLM suite + one live
  end-to-end test
- `docs/` — `assignment.md` (brief), `technical-decisions.md` (why),
  plus later: `security-limitations.md`, `governance-checklist.md`,
  `evaluation.md`, `azure-deployment.md`
- `PLAN.md` — phased implementation plan (source of truth for build order)
- `docker-compose.yml` / `Dockerfile` — local and containerized run paths

As of now, only the Phase 0 scaffold above exists — module directories
are empty stubs pending Phase 1 onward. See `PLAN.md` for phase status.

## Rules
- After each phase, explicitly test the output, sanity-check assumptions,
  and self-review against the evaluation criteria in CLAUDE.md before you proceed. 
- Never fabricate a citation. If nothing relevant was retrieved, say so
  explicitly instead of answering anyway.
- Treat groundedness as the core requirement, not a stretch feature — it's
  worth more to get this right than to add RAG/agents on a shaky base.
- Keep secrets (AAD, Key Vault, API keys) out of source control — `.env`
  only, and `.env` is gitignored.
- Any non-obvious design decision goes in the README, not just in chat —
  the interview will reference the README
- Follow the best software engineering an UI/UX practices (coding, design, logging, testing, layouts, clean code, readable, maintainable project)

## Documentation
 - maintain a running decisions/methodology log that will be used for presentation
   at the end. After everything is implemented, it has to be possible to draft the
   narrative: problem, approach, key decisions, trade-offs, results.
