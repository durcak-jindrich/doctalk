# DocTalk — Discuss Your Documents

Grounded Q&A over a small set of internal documents. Upload 1–5 files
(PDF/DOCX/MD), ask questions, and get answers sourced **only** from that
content, with citations back to the originating chunk/document. If nothing
relevant is found, DocTalk says so instead of guessing.

> **Status: early build (Phase 3 of `PLAN.md`).** Ingestion, hybrid retrieval,
> and grounded synthesis with validated citations work end to end at the code
> level. No API/UI yet, and not yet wrapped in the LangGraph graph — see
> [Project status](#project-status).

Built as an interview case study; the brief is in
[`docs/assignment.md`](docs/assignment.md).

## Contents
- [Architecture](#architecture)
- [How groundedness is enforced](#how-groundedness-is-enforced)
- [Tech stack](#tech-stack)
- [Quickstart](#quickstart)
- [Repository layout](#repository-layout)
- [Key decisions & assumptions](#key-decisions--assumptions)
- [Limitations](#limitations)
- [Project status](#project-status)

## Architecture

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

Everything downstream of "Ask" runs through one LangGraph graph — no separate
simple/agentic mode.

## How groundedness is enforced

Four independent gates, because "the prompt says not to hallucinate" is not a
control:

1. **Retrieval gate (pre-LLM).** Nothing retrieved, or the best rerank score
   below `MIN_RERANK_SCORE`, and DocTalk refuses without spending a call.
   Dense search always returns *something*, so the score is what's checked.
2. **The prompt.** Numbered sources, cite every claim, reply
   `INSUFFICIENT_CONTEXT` rather than fill a gap. Source text is framed as
   data, not instruction, so an "ignore your instructions" line inside an
   uploaded document is treated as quoted material.
3. **Deterministic citation validation.** Every `[n]` is resolved in code
   against the exact chunks sent to the model; an unresolvable marker is a
   fabricated citation, and an answer with no markers counts as ungrounded.
4. **Bounded retry, then refusal.** One corrective retry, then the draft is
   withheld — never shown with the bad citation quietly stripped out.

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
| Testing | `pytest`, fake-LLM fast suite; live-LLM tests opt-in (`-m live`) |
| Config/secrets | `.env` (gitignored) via `pydantic-settings`; Key Vault + Managed Identity on Azure |

Rationale, alternatives, and trade-offs:
[`docs/technical-decisions.md`](docs/technical-decisions.md).

## Quickstart

**Requirements:** Docker (recommended), or Python 3.12 + [`uv`](https://docs.astral.sh/uv/).

```bash
cp .env.example .env    # add OPENROUTER_API_KEY (free tier works)
```

**Everything (app + Postgres/ParadeDB) via Docker:**
```bash
docker compose up --build
# `migrate` bootstraps the schema, then `app` starts on :8000 (/health)
# Fresh start: docker compose down -v && docker compose up -d
```

**API against a Postgres you manage yourself:**
```bash
uv sync
uv run python -m scripts.init_db   # one-time schema bootstrap
uv run uvicorn app.main:app --reload
```

**Tests / lint:**
```bash
uv run pytest        # fast suite; no LLM calls, no API key needed
uv run pytest -m live  # adds the live-LLM tests (spends OpenRouter quota)
uv run ruff check .
```

**Manual walkthroughs** — print the pipeline's internals step by step, and are
how each phase was verified. Both reset the document workspace:
```bash
docker compose up -d db
uv run python -m scripts.manual_smoke_test                    # ingestion + retrieval
uv run python -m scripts.manual_smoke_test_synthesis          # + governance, fake LLM
uv run python -m scripts.manual_smoke_test_synthesis --live   # + real answers (3 LLM calls)
```

*(Today `/health` is the only HTTP endpoint — ingestion, retrieval and
synthesis run as Python functions but aren't wired to routes yet. The
upload → ask → citations demo script lands with Phase 5.)*

## Repository layout

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI entrypoint |
| `app/config.py` | Settings (`pydantic-settings`, from `.env`) |
| `app/api/` | HTTP routes: upload, ask, document list/view/delete |
| `app/parsers/` | PDF/DOCX/MD → structured text |
| `app/chunking/` | Structure-aware chunking + chunk ID scheme |
| `app/storage/` | Postgres/`pgvector` persistence, workspace cap |
| `app/retrieval/` | Hybrid dense+lexical retriever, RRF fusion, reranking |
| `app/llm/` | `LLMClient` interface + OpenRouter adapter |
| `app/synthesis/` | Grounding prompt, citation validation, refusal policy |
| `app/graph/` | LangGraph graph: retrieve → synthesize → governance |
| `scripts/init_db.py` | One-shot schema bootstrap, run before the backend starts |
| `scripts/manual_smoke_test*.py` | Manual phase-verification walkthroughs |
| `tests/` | Fake-LLM fast suite + opt-in live tests |
| `docs/` | Brief, technical decisions, and (later) security/governance/eval |
| `PLAN.md` | Phased build plan — source of truth for what's done vs pending |

## Key decisions & assumptions

Detail in [`docs/technical-decisions.md`](docs/technical-decisions.md); the
headlines:

- **One bounded 5-document workspace**, not a growing corpus. Uploads past the
  cap are rejected until a document is removed.
- **Hybrid retrieval** (dense + BM25, RRF-fused, reranked) — lexical-only
  misses paraphrases, dense-only is weak on exact IDs and numbers.
- **Citations travel as source numbers**, not chunk IDs: the model cites `[2]`
  and the number → `chunk_id` mapping is rebuilt in code, so a citation cannot
  name a chunk that wasn't retrieved. The UI shows human-readable provenance
  (`handbook.pdf > Sick Leave (p. 4)`) with the `chunk_id` behind it.
- **Uploaded text is untrusted input** — the prompt frames sources as data, and
  the synthesis smoke test includes a document that tries to override it.
- **Schema bootstrap is an explicit pre-start step**, not lazy creation on
  first ingest.
- **Dedup is keyed on (filename, content)** — re-uploading identical bytes is a
  no-op, but the same file renamed takes a second slot.
- **OpenRouter behind an `LLMClient` interface**; Azure OpenAI is the
  documented production alternative.

## Limitations

- No API/UI yet — the pipeline runs as direct Python calls.
- **Citation validation proves resolution, not entailment**: it guarantees the
  cited chunk was retrieved and shown to the model, not that it supports the
  claim. Faithfulness is measured in the Phase 9 evaluation.
- **`MIN_RERANK_SCORE` is a tuned heuristic** (`-5.0`, from observed
  separation: covered ≈ +7 to +9, in-domain-but-uncovered ≈ −6, nonsense ≈
  −11). Too high and answerable questions get refused; re-tuned in Phase 9.
- **Prompt-injection resistance is best-effort.** No prompt-level defence is
  airtight; the real containment is that a successful injection still cannot
  manufacture a valid citation.
- **Free OpenRouter slugs churn and throttle** — retired without notice,
  rate-limited upstream. A funded key and a paid model is the fix for a demo.
- PDF extraction assumes a text layer; scanned/image-only PDFs (no OCR) fail
  ingestion with an explicit error rather than a silently empty document.
- Embedding width is baked into the `chunks.embedding` column at schema
  creation — changing `EMBEDDING_MODEL` to a different width needs the table
  dropped.

## Project status

Tracking [`PLAN.md`](PLAN.md). Current: **Phase 3 — Grounded synthesis &
citation governance** (complete, not yet wired to an API).

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
- [`docs/technical-decisions.md`](docs/technical-decisions.md) — why each choice
- [`PLAN.md`](PLAN.md) — phased build plan
- [`CLAUDE.md`](CLAUDE.md) — working project conventions
