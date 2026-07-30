# DocTalk — Discuss Your Documents

Upload 1–5 documents (PDF/DOCX/MD), ask questions, get answers grounded
**only** in that content, with every claim cited back to the chunk it came
from. When the documents don't cover a question, DocTalk says so instead of
guessing — that refusal is the feature, not an error path.

**Status:** baseline plus all four stretch options are built (Phases 0–10 of
[`PLAN.md`](PLAN.md)) — upload → ask → cited answer runs locally end to end,
instrumented, tested and evaluated. The Azure path (AAD auth, Key Vault,
Bicep IaC) is written and syntax-checked in CI but has never been deployed to
a live subscription. Only presentation prep remains — see
[Project status](#project-status).

## Quickstart
Requirements: Docker 

```bash
cp .env.example .env          # add OPENROUTER_API_KEY — a free tier key works
docker compose up --build     # `migrate` applies migrations/ (~1s), then the app serves :8000
```

Open <http://localhost:8000>

For fresh setup:
`docker compose down -v && docker compose up --build`.

| Task | Command |
|---|---|
| Run everything (app + ParadeDB) | `docker compose up --build` |
| Test — no LLM calls, no API key (needs `db` up) | `uv run pytest` |
| Test the live LLM integration — spends quota | `uv run pytest -m live` |
| Lint / format | `uv run ruff check .` / `uv run ruff format .` |
| Reset the schema | `uv run python -m scripts.reset_db` |
| Regenerate the eval report | `uv run python -m scripts.evaluate [--live]` |
| Step through the pipeline manually | `uv run python -m scripts.manual_smoke_test[_synthesis]` |

## Demo script

`docker compose up --build`, then <http://localhost:8000>.

1. **Upload** `samples/hr-policy.md` and `samples/it-security.md` — each reports
   its chunk count and the slot counter moves to `2 / 5`.
2. **Ask something covered** — *"How many vacation days do full-time employees
   get?"* The answer carries `[n]` chips; clicking one opens the exact passage,
   its `chunk_id`, and which retrieval leg found it.
3. **Ask something absent** — *"What is the parental leave allowance?"*
   DocTalk refuses and says why. Styled as a normal outcome, not a failure.
4. **Summarize** — *"Summarize the documents"* routes to the summarize tool;
   the graph path shows `gather_summary_sources` instead of `retrieve`.
5. **Open "Observability details"** — route, latency, tokens, cost, per-node timings.
   A repeated `draft → govern` pair means the citation validator rejected the
   first draft and sent it back.

### Sample documents

`samples/` holds one document per supported format — exactly a full workspace
(5), each producing several citable chunks:

| File | Format | Chunks | What it exercises |
|---|---|---|---|
| `hr-policy.md` | MD | 2 | Vacation and sick leave — the demo's answerable questions |
| `it-security.md` | MD | 2 | Exact terms (password length): the lexical leg's home ground |
| `product-faq.md` | MD | 2 | A paraphrased question with no term overlap: the dense leg carries it |
| `onboarding-guide.docx` | DOCX | 6 | Heading styles → nested `section_path` in citations |
| `data-retention-policy.pdf` | PDF | 5 | Multi-page text layer → `page_number` in citations |

The three Markdown files are also the golden corpus that
`tests/integration/test_golden_qa.py` and
[`docs/evaluation.md`](docs/evaluation.md) score against — editing one changes
what those assert. The DOCX and PDF are demo-only and generated from
`scripts/make_samples.py`; edit the text there and re-run
`uv run --with reportlab python -m scripts.make_samples` rather than editing
the binaries.

## Architecture

```
Upload (PDF/DOCX/MD)
   → parse (pdfplumber / python-docx / plain read)
   → structure-aware chunk (+ chunk ID)
   → store (Postgres: documents, chunks, pgvector embeddings)

Ask → one LangGraph graph:
   route ─────────────── question vs. whole-workspace summary (regex, no LLM call)
     ├─ retrieve ─────── dense (pgvector) + lexical (pg_search BM25), RRF-fused,
     │                   cross-encoder reranked; refuses here, pre-LLM, if
     │                   nothing relevant was found
     └─ gather_summary_sources ── each document's opening chunks, budget split
                                  across the workspace
   → draft ──────────── LLM answers strictly from the selected sources
   → govern ─────────── every citation must resolve to a source actually sent;
                        one correction back to `draft`, then refuse
   → answer + citations + trace (node path, attempts, latency, tokens, cost)
```

One graph is the whole `/ask` pipeline — there is no separate "simple" mode,
and both routes pass through the same `govern` node, so a summary is cited or
refused exactly like an answer. The corrective retry is a real edge, so
`answer.steps` shows whether an answer needed one.

## How groundedness is enforced

Four independent gates, because "the prompt says not to hallucinate" is not a
control:

1. **Retrieval gate (pre-LLM)** — nothing retrieved, or best rerank score
   below `MIN_RERANK_SCORE` → refuse without spending a call. Dense search
   always returns *something*, so the score is the signal, not the row count.
2. **Prompt** — numbered sources, cite every claim, emit `INSUFFICIENT_CONTEXT`
   rather than fill a gap. Sources are framed as data, so an "ignore your
   instructions" line inside an uploaded file reads as quoted material.
3. **Deterministic validation** — every `[n]` is resolved in code against the
   exact chunks sent to the model. An unresolvable marker is a fabricated
   citation; an answer with no markers counts as ungrounded.
4. **Bounded retry, then refusal** — one correction, then the draft is
   withheld. Never shown with the bad citation quietly stripped out.

Why each was built this way:
[`docs/technical-decisions.md`](docs/technical-decisions.md#grounded-synthesis--citation-governance).

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, LangGraph |
| Parsing | `pdfplumber` (PDF), `python-docx` (DOCX), plain read (MD) |
| Storage | Postgres + `pgvector` (dense) + `pg_search`/ParadeDB (BM25) |
| LLM | `LLMClient` interface → OpenRouter adapter (model set via env) |
| Frontend | Static HTML/CSS/vanilla JS served by FastAPI — no build step |
| Containers | Docker Compose locally; Dockerfile + Bicep for Azure Container Apps |
| Testing | `pytest`, fake-LLM fast suite; live tests opt-in (`-m live`) |
| Config | `.env` via `pydantic-settings`; Key Vault + Managed Identity on Azure |

## Testing

```bash
docker compose up -d db             # tests/integration/ and tests/e2e/ need a real Postgres
uv run playwright install chromium  # once — tests/e2e/ skips itself without a browser
uv run pytest                       # fast suite: no LLM calls, no API key, deterministic
uv run pytest -m live               # ~10 real OpenRouter calls; skips cleanly without a key
uv run pytest tests/unit            # unit tests alone need nothing running
```

`tests/e2e/` is the demo script in a browser: it empties the workspace through
the UI, uploads one document per format, asks a question of each and opens the
citations, then asks something the corpus does not cover and checks the
refusal. Fake model by default, the real one under `-m live` — where the
answers themselves are asserted. It resets the local workspace.

## Observability

Every answer carries its own trace, in the UI and in the logs.

**UI** — "Observability details" per answer: route, total vs. LLM vs. retrieval time,
tokens, cost, and the graph path with a per-node timing bar and verdict. A
repeated `draft → govern` pair is highlighted as a corrective retry.

## Evaluation

`scripts/evaluate.py` scores the golden Q&A set (`tests/golden.py` — the same
cases the integration suite asserts on) through the production graph, and
writes [`docs/evaluation.md`](docs/evaluation.md): routing/outcome/retrieval
accuracy, retrieval-leg contribution, latency breakdown, cost, and a
`MIN_RERANK_SCORE` sensitivity sweep.

```bash
docker compose up -d db
uv run python -m scripts.evaluate          # fake LLM — no quota spent
uv run python -m scripts.evaluate --live   # real model, ~5–10 calls
```

## Azure deployment

Container Apps + Azure Database for PostgreSQL + Key Vault + Entra ID, via
Bicep in `infra/`. **Written and syntax-checked** by `az bicep build` in CI.
Walkthrough, prerequisites and gaps:
[`docs/azure-deployment.md`](docs/azure-deployment.md).

- **Auth** — `AUTH_ENABLED=true` validates Entra ID bearer tokens
  (`app/api/auth.py`) on every `/api/*` route; off by default, so a local run
  needs no tenant. `/health` and the static frontend stay open.
- **Secrets** — with `AZURE_KEY_VAULT_URL` set, `app/config.py` loads
  `DATABASE_URL`/`OPENROUTER_API_KEY` from Key Vault via the Container App's
  user-assigned managed identity before `Settings` is built.
- **CI/CD** — `.github/workflows/`: `ci.yml` (`az bicep build`) on every push;
  `deploy.yml` (manual: build/push to ACR, then deploy the template). Lint and
  tests run locally — see [Testing](#testing).

## Repository layout

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI entrypoint: routers, static frontend, model warmup |
| `app/config.py` | `pydantic-settings` config; Key Vault overlay |
| `app/observability.py` | Structured logging, trace ids, per-node step records |
| `app/api/` | Routes, schemas, dependencies, Entra ID token validation |
| `app/static/` | Frontend — HTML/CSS/vanilla JS, no build step |
| `app/parsers/` | PDF/DOCX/MD → structured text |
| `app/chunking/` | Structure-aware chunking + chunk ID scheme |
| `app/storage/` | Postgres/`pgvector` persistence, workspace cap |
| `app/retrieval/` | `HybridRerankRetriever`: dense + lexical, RRF, rerank |
| `app/llm/` | `LLMClient` interface + OpenRouter adapter |
| `app/synthesis/` | Prompt, citation validator, refusal vocabulary (primitives) |
| `app/graph/` | The `/ask` graph — `answer_question()` is the entry point |
| `samples/` | Demo documents, one per format; the `.md` trio is `tests/golden.py`'s corpus |
| `migrations/` | Versioned schema SQL + `apply.sh` (the `migrate` service) |
| `scripts/` | `reset_db`, `evaluate`, `make_samples`, manual smoke tests |
| `tests/` | `unit/`, `integration/`, opt-in `live/`; `golden.py` fixtures |
| `infra/` | Bicep: Container Apps, Postgres, Key Vault, identity + RBAC, ACR |
| `docs/` | Brief, decisions, Azure, evaluation, security, governance |

## Assumptions & limitations

- **One bounded 5-document workspace**, not a growing corpus; uploads past the
  cap are rejected until a document is removed.
- **Schema changes are versioned migrations**, applied before the app starts,
  never created lazily on first ingest.

Known limitations:

- **Single shared workspace** — every caller sees and can delete every
  document.
- **Citation validation proves resolution, not entailment** — the cited chunk
  was retrieved and shown, not that it supports the claim.
- **Free OpenRouter slugs churn and throttle**; Prefer a funded API key.
- **PDFs need a text layer** — no OCR, so a scanned PDF fails ingestion with an
  explicit error rather than becoming a silently empty document.
- **Embedding width is fixed in the schema** (`VECTOR(384)`) — a different-width
  `EMBEDDING_MODEL` needs a new migration.

## Documentation map

| Document | Contents |
|---|---|
| [`docs/technical-decisions.md`](docs/technical-decisions.md) | Every design decision: choice, reason, trade-off |
| [`docs/evaluation.md`](docs/evaluation.md) | Golden-set report (generated by `scripts/evaluate.py`) |
| [`docs/security-limitations.md`](docs/security-limitations.md) | Trust boundaries, injection surfaces, data handling, gaps |
| [`docs/governance-checklist.md`](docs/governance-checklist.md) | Data-asset entry draft + controls checklist |
| [`docs/azure-deployment.md`](docs/azure-deployment.md) | Azure walkthrough, prerequisites, what's not implemented |
| [`docs/assignment.md`](docs/assignment.md) | The original brief |
| [`PLAN.md`](PLAN.md) | Phased build plan and phase status |
| [`CLAUDE.md`](CLAUDE.md) | Working conventions for coding agents |

## Project status

| Phase | Status |
|---|---|
| 0 — Setup & infrastructure | Done |
| 1 — Ingestion & storage | Done |
| 2 — Hybrid retrieval + reranking | Done |
| 3 — Grounded synthesis & citation governance | Done |
| 4 — Agentic orchestration (LangGraph) | Done |
| 5 — API + frontend | Done |
| 6 — Observability instrumentation | Done |
| 7 — Testing | Done |
| 8 — Azure readiness (written, not deployed) | Done |
| 9 — Evaluation | Done |
| 10 — Documentation | Done |
| 11 — Presentation prep | Not started |
