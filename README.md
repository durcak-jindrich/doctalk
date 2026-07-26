# DocTalk — Discuss Your Documents

Grounded Q&A over a small set of internal documents. Upload 1–5 files
(PDF/DOCX/MD), ask questions, and get answers sourced **only** from that
content, with citations back to the originating chunk/document. If nothing
relevant is found, DocTalk says so instead of guessing.

> **Status: baseline complete (Phase 5 of `PLAN.md`).** Upload → ask →
> cited answer runs end to end in the browser. Observability, Azure IaC and
> the evaluation report are still to come — see
> [Project status](#project-status).

Built as an interview case study; the brief is in
[`docs/assignment.md`](docs/assignment.md).

## Contents
- [Architecture](#architecture)
- [How groundedness is enforced](#how-groundedness-is-enforced)
- [Tech stack](#tech-stack)
- [Quickstart](#quickstart)
- [Demo script](#demo-script)
- [LLM calls in tests](#llm-calls-in-tests)
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
       route     (question vs. whole-workspace summary — deterministic,
                  costs no LLM call)
         ├ retrieve (dense pgvector + lexical pg_search, RRF-fused,
         │           cross-encoder reranked; refuses here if nothing
         │           relevant was found, before any LLM call)
         └ gather_summary_sources (the summarize tool: each document's
                     opening chunks, budget split across the workspace)
       → draft   (LLM answers strictly from the selected sources)
       → govern  (validates every citation resolves to a real source;
                  sends one correction back to `draft`, then refuses)
   → Answer + citations (+ observability metadata: node path, attempts,
                          latency, tokens, cost)
```

Everything downstream of "Ask" runs through one LangGraph graph — no separate
simple/agentic mode, and both routes share the one `govern` node, so a summary
is cited or refused exactly like an answer. The corrective retry is a real
edge, so `answer.steps` shows whether an answer needed one.

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
# `migrate` applies migrations/ (~1s), then `app` serves the UI on :8000
# Fresh start: docker compose down -v && docker compose up -d
```

**API against a Postgres you manage yourself:**
```bash
uv sync
uv run python -m scripts.reset_db   # drop + replay migrations/ (destructive)
uv run uvicorn app.main:app --reload   # UI + API on http://localhost:8000
```

Startup loads the embedding and reranker models (a few seconds) so the first
question isn't the one that pays for it. The app boots without an
`OPENROUTER_API_KEY` — upload and retrieval work; `/api/ask` returns 503 until
a key is set.

**Tests / lint:**
```bash
uv run pytest        # fast suite — no LLM calls, no API key needed
uv run ruff check .
```

### LLM calls in tests

**`uv run pytest` never calls OpenRouter.** The suite runs against a fake
`LLMClient`, so it needs no API key, spends no quota, and stays fast and
deterministic. This is enforced, not a convention: `addopts = -m 'not live'`
in `pyproject.toml` deselects live tests unless you ask for them.

**Exactly one test makes a real call** — `tests/live/test_synthesis_live.py`,
marked `live`. Run it when the OpenRouter integration itself is what you want
to check: after changing `LLM_MODEL`, the adapter, or the grounding prompt,
and once before a demo. Not on every commit, and not in the normal dev loop.

```bash
uv run pytest -m live    # 1 real call; skips cleanly if no API key is set
```

It asserts structure only — that a real model answers with a citation that
resolves, and that usage is captured — never particular wording, so it does
not break when the model changes. Everything a real provider *can't* be made
to produce on demand (no choices, null content, rate-limit and retired-slug
errors) is covered offline in `tests/unit/test_openrouter_client.py`.

**Manual walkthroughs** — print the pipeline's internals step by step, and are
how each phase was verified. Both reset the document workspace:
```bash
docker compose up -d db
uv run python -m scripts.manual_smoke_test                    # ingestion + retrieval
uv run python -m scripts.manual_smoke_test_synthesis          # + graph, fake LLM
uv run python -m scripts.manual_smoke_test_synthesis --live   # + real answers (~6 LLM calls)
```

## Demo script

`docker compose up --build`, then open <http://localhost:8000>.

1. **Upload** — drop `hr-policy.md` and `it-security.md` onto the panel. Each
   file reports its chunk count; the counter moves to `2 / 5`.
2. **Ask a covered question** — *"How many vacation days do full-time
   employees get?"* The answer carries `[n]` chips; clicking one opens the
   exact passage it came from, with its `chunk_id` and which retrieval leg
   found it.
3. **Ask something absent** — *"What is the parental leave allowance?"* DocTalk
   refuses and says why, instead of guessing. This is the point of the system:
   the refusal is styled as a normal outcome, not an error.
4. **Summarize** — *"Summarize the documents"* routes to the summarize tool;
   **Under the hood** shows `gather_summary_sources` in the graph path
   instead of `retrieve`.
5. **Open "Under the hood"** on any answer — route, latency, tokens, cost, and
   the graph path. A repeated `draft → govern` pair means the citation
   validator rejected the first attempt and sent it back for correction.

## HTTP API

`/docs` serves the generated OpenAPI reference.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/documents` | Upload one or more files; per-file outcome + workspace state |
| `GET` | `/api/documents` | Documents and remaining capacity |
| `GET` | `/api/documents/{id}` | One document with its chunks |
| `DELETE` | `/api/documents/{id}` | Remove a document, freeing its slot |
| `POST` | `/api/ask` | Question → grounded answer, citations, observability |
| `GET` | `/health` | Liveness |

A refusal is a **200**, not an error: "the documents don't answer this" is the
product working. Only a broken provider is a 5xx.

## Repository layout

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI entrypoint |
| `app/config.py` | Settings (`pydantic-settings`, from `.env`) |
| `app/api/` | HTTP routes, request/response schemas, dependencies |
| `app/static/` | Frontend: HTML/CSS/vanilla JS, no build step |
| `app/parsers/` | PDF/DOCX/MD → structured text |
| `app/chunking/` | Structure-aware chunking + chunk ID scheme |
| `app/storage/` | Postgres/`pgvector` persistence, workspace cap |
| `app/retrieval/` | Hybrid dense+lexical retriever, RRF fusion, reranking |
| `app/llm/` | `LLMClient` interface + OpenRouter adapter |
| `app/synthesis/` | Grounding prompts, citation validation, refusal vocabulary |
| `app/graph/` | The `/ask` graph: route → retrieve/summarize → draft → govern |
| `migrations/` | Versioned schema SQL + `apply.sh`, the psql runner the `migrate` service uses |
| `scripts/reset_db.py` | Drop and replay all migrations — local dev only |
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
- **Schema changes are versioned migrations**, applied by a pre-start step that
  runs on the database image — never created lazily on first ingest.
- **Dedup is keyed on (filename, content)** — re-uploading identical bytes is a
  no-op, but the same file renamed takes a second slot.
- **OpenRouter behind an `LLMClient` interface**; Azure OpenAI is the
  documented production alternative.

## Limitations

- **Single shared workspace, no auth** — anyone reaching the port sees and can
  delete every document. AAD auth arrives in Phase 8.
- **No streaming** — answers appear when governance has passed, which is the
  price of never showing an unvalidated citation.
- **Summaries cover each document's opening, not the whole document** — the
  prompt says so, but a summary is a partial view by construction.
- **Summary routing is pattern-based**, so an unusual phrasing falls through
  to retrieval; it still answers, just from ranked chunks.
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

Tracking [`PLAN.md`](PLAN.md). Current: **Phase 5 — API + frontend**.
The baseline the brief asks for — upload, ask, cited answers, runs locally
end to end — is complete.

| Phase | Status |
|---|---|
| 0 — Setup & infrastructure | Done |
| 1 — Ingestion & storage | Done |
| 2 — Hybrid retrieval + reranking | Done |
| 3 — Grounded synthesis & citation governance | Done |
| 4 — Agentic orchestration (LangGraph) | Done |
| 5 — API + frontend | Done |
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
