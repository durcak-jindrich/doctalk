# DocTalk — Discuss Your Documents

Grounded Q&A over a small set of internal documents. Upload 1–5 files
(PDF/DOCX/MD), ask questions, and get answers sourced **only** from that
content, with citations back to the originating chunk/document. If nothing
relevant is found, DocTalk says so instead of guessing.

> **Status: early build (Phase 3 of `PLAN.md`).** Ingestion (parse → chunk →
> embed → store), hybrid retrieval (dense + lexical, RRF-fused, cross-encoder
> reranked), and grounded synthesis with validated citations all work end to
> end at the code level. There is no API/UI yet, and the pipeline is not yet
> wrapped in the LangGraph graph — see [Project status](#project-status) for
> exactly what runs today.

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

### How groundedness is enforced

Four independent gates, because "the prompt says not to hallucinate" is not
a control:

1. **Retrieval gate (before any LLM call).** No chunks retrieved, or the best
   cross-encoder score below `MIN_RERANK_SCORE`, and DocTalk refuses without
   spending a call. Dense search always returns *something*, so the relevance
   score — not the presence of rows — is what's checked.
2. **The prompt.** The model is given a numbered SOURCES list and told to
   answer only from it, to cite every claim, and to reply `INSUFFICIENT_CONTEXT`
   rather than fill a gap. Source text is explicitly framed as data, not
   instruction, so an "ignore your instructions" line inside an uploaded
   document is treated as quoted material.
3. **Deterministic citation validation.** Every `[n]` marker is resolved in
   code against the exact chunk list that was sent to the model. A marker that
   doesn't resolve is a fabricated citation, and an answer with no markers at
   all counts as ungrounded. This checks that a citation *resolves* — whether
   the cited passage supports the claim is faithfulness, measured separately in
   the Phase 9 evaluation.
4. **Bounded retry, then refusal.** A failed validation buys exactly one
   corrective retry; after that the draft is withheld and the user sees an
   explicit refusal. An unvalidated answer is never shown with the bad citation
   quietly stripped out.

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
# `migrate` bootstraps the schema against `db`, then `app` starts.
# API at http://localhost:8000, health check at /health
# Tear down and start fresh: docker compose down -v && docker compose up -d
```

**Run the API locally against a Postgres you manage yourself:**
```bash
uv sync
uv run python -m scripts.init_db   # one-time schema bootstrap
uv run uvicorn app.main:app --reload
# Remove only data from DB: docker compose exec db psql -U doctalk -d doctalk -c "DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS documents;"
```

**Tests / lint:**
```bash
uv run pytest
uv run ruff check .
```

**Manual end-to-end walkthroughs** (these print the pipeline's internals step
by step, and are how each phase was verified — both are destructive to the
document workspace):
```bash
docker compose up -d db
uv run python -m scripts.manual_smoke_test             # ingestion + retrieval
uv run python -m scripts.manual_smoke_test_synthesis   # + grounded answers, refusals, governance
```

*(Today, `/health` is the only HTTP endpoint — ingestion, retrieval and
synthesis work as Python functions (`ingest_document()`, `retrieve()`,
`synthesize()`) but aren't wired to API routes yet. This section will gain the
actual demo script — upload → ask → citations — once Phase 5 lands.)*

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
| `app/synthesis/` | Grounding prompt, citation parsing/validation, refusal policy |
| `app/graph/` | LangGraph graph: retrieve → synthesize → governance |
| `scripts/init_db.py` | One-shot DB schema bootstrap (extensions/tables/indexes) — run before the backend starts |
| `scripts/manual_smoke_test.py` | Manual end-to-end walkthrough of ingestion + retrieval (prints DB/vector/retrieval-leg state; destructive to the workspace) |
| `scripts/manual_smoke_test_synthesis.py` | Manual walkthrough of grounded answering: citations, both refusal gates, prompt injection, the governance retry loop |
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
- **Schema bootstrap is an explicit pre-start step** (`scripts/init_db.py`,
  run as its own `migrate` service before `app` starts in Docker Compose) —
  not created lazily on first ingest. Keeps schema readiness a deploy-time
  guarantee rather than a side effect of whichever request happens to
  arrive first.
- **Upload dedup is keyed on (filename, content), not content alone:**
  `document_id` is `<filename-slug>-<content-hash>`, so re-uploading identical
  bytes is a no-op, but the *same* file renamed is treated as a second
  document and occupies a second workspace slot. Accepted for a 5-document
  workspace where the filename is part of how a user identifies a source.
- **Groundedness is non-negotiable:** the LLM answers only from retrieved
  chunks; a deterministic governance step validates every citation against
  what was actually retrieved and refuses rather than fabricates when
  nothing relevant is found. See
  [How groundedness is enforced](#how-groundedness-is-enforced).
- **Citations are source numbers, not chunk IDs, on the wire.** The model
  cites `[2]` against a numbered source list, and the number → `chunk_id`
  mapping is rebuilt in code from the exact list it was sent — so a citation
  physically cannot name a chunk that wasn't retrieved. The UI still shows the
  human-readable source (`handbook.pdf > Sick Leave (p. 4)`) with the
  `chunk_id` behind it.
- **Uploaded document text is untrusted input.** The grounding prompt frames
  sources as data rather than instruction, and the synthesis smoke test
  includes a document that attempts to override the system prompt.
- **LLM provider:** OpenRouter for local dev/demo, behind an `LLMClient`
  interface; direct Azure OpenAI documented as the production alternative.
- **Runtime pinned to Python 3.12**, not the system's 3.14, because
  `sentence-transformers` has no 3.14 wheel yet.

## Limitations

*(Will expand as later phases land — e.g. reranker context-window limits,
multi-lingual support.)*

- No API/UI yet — the pipeline only runs as direct Python calls, see
  [Project status](#project-status).
- **Citation validation proves resolution, not entailment.** It guarantees a
  cited chunk was genuinely retrieved and shown to the model; it does not
  verify that the chunk supports the claim. Faithfulness is measured
  separately in the Phase 9 evaluation.
- **The refusal threshold (`MIN_RERANK_SCORE`) is a tuned heuristic.** Set at
  `-5.0` against observed score separation (covered questions score ≈ +7 to
  +9, an in-domain-but-uncovered question ≈ −6, nonsense ≈ −11). Set it too
  high and DocTalk refuses answerable questions; it is re-tuned against the
  Phase 9 eval set.
- **Prompt-injection resistance is best-effort, not a guarantee.** The
  grounding rules hold against the injected document in the smoke test, but
  no prompt-level defence is airtight — the real containment is that the model
  can only cite retrieved chunks, so a successful injection still cannot
  manufacture a valid citation.
- **Free OpenRouter model slugs churn and throttle.** They get retired without
  notice and rate-limit upstream; failures surface as an explicit error naming
  the fix. A funded key and a paid model is the fix for a live demo.
- Single bounded 5-document workspace by design, not a document library
  (see above) — not a limitation to "fix," a scope decision.
- PDF text extraction (`pdfplumber`) assumes a text layer; scanned/image-only
  PDFs (no OCR step) will extract no text and fail ingestion with an
  explicit error rather than silently producing an empty document.
- The embedding vector width is derived from `EMBEDDING_MODEL` (read off the
  loaded model) but baked into the `chunks.embedding` column at
  schema-creation time — switching to a model with a different output
  dimension requires dropping the `chunks` table (see
  `docs/technical-decisions.md`).
- OpenRouter free-tier models are rate-limited and lower-quality than
  paid alternatives; acceptable for a demo, flagged as a production
  swap-out.

## Project status

Tracking [`PLAN.md`](PLAN.md)'s phases. Current: **Phase 3 — Grounded
synthesis & citation governance** (complete, not yet wired to an API).

| Phase | Status |
|---|---|
| 0 — Setup & infrastructure | Done |
| 1 — Ingestion & storage | Done |
| 2 — Hybrid retrieval + reranking | Done |
| 3 — Grounded synthesis & citation governance | Done |
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
