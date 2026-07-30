# Implementation Plan

Build order and phase status. What the system *is* and how to run it:
[`README.md`](README.md). Why each choice was made:
[`docs/technical-decisions.md`](docs/technical-decisions.md).

**Scope decision:** all four stretch options in the brief (RAG, LangGraph
orchestration, Azure-readiness, observability) are treated as required
deliverables, not extras. Phase order follows build dependencies only —
retrieval before orchestration, orchestration before observability.

## Phases

| # | Phase | Scope | Status |
|---|---|---|---|
| 0 | Setup & infrastructure | Repo scaffold, `pyproject.toml`, Docker Compose (`app` + `paradedb/paradedb`), `.env.example`, versioned `migrations/` applied by a one-shot `migrate` service | Done |
| 1 | Ingestion & storage | PDF/DOCX/MD parsers → structure-aware chunker → document/chunk ID scheme → Postgres persistence with the 5-document cap | Done |
| 2 | Hybrid retrieval + reranking | Dense (`pgvector`) + lexical (`pg_search`) search, RRF fusion, cross-encoder rerank, as a standalone `Retriever` | Done |
| 3 | Grounded synthesis & citation governance | `LLMClient`/OpenRouter adapter, grounding prompt, deterministic citation validator, explicit refusal path | Done |
| 4 | Agentic orchestration | LangGraph graph (route → retrieve \| summarize → draft → govern) as the production `/ask` entry point | Done |
| 5 | API + frontend | Upload/ask/list/view/delete endpoints; drag-drop upload, chat-style Q&A, citation chips, observability panel. **First `README.md` draft written here** — the baseline now runs end to end | Done |
| 6 | Observability | Per-node timing and token capture, JSON-lines logs, trace correlation, wired into the UI panel and the eval | Done |
| 7 | Testing | Unit (parsers, chunker, retrieval legs, citation validator, graph, adapter errors); integration golden Q&A set against real Postgres; opt-in `live` end-to-end test | Done |
| 8 | Azure readiness | Dockerfile, Bicep IaC, Key Vault/Managed Identity, AAD auth, `docs/azure-deployment.md`, example workflows. **Written and `az bicep build`-checked in CI, never deployed** | Done |
| 9 | Evaluation | `scripts/evaluate.py` over the golden set: routing/outcome/retrieval/faithfulness rates, leg contribution, latency, cost, threshold sweep → `docs/evaluation.md` | Done |
| 10 | Documentation | Consolidate README (architecture, demo, assumptions, limitations), `docs/security-limitations.md`, `docs/governance-checklist.md`, `CLAUDE.md` | Done |

**README is a baseline deliverable, not a Phase 10 artifact.** It was drafted
at the end of Phase 5 and updated every phase since, so it never describes a
system that doesn't exist yet.

**Version control:** one commit per phase-sized logical unit, not one large
commit.

## Assumptions to flag to the Product Owner

- **Workspace:** one bounded, 5-document working set, persisted across
  restarts — not a growing corpus or a multi-session library.
- **Azure `pg_search`:** not on Azure Postgres' extension allow-list, so the
  lexical leg needs a `ts_rank` fallback there. Confirm against current Azure
  docs; it blocks a clean deploy today.
- **Azure deployment:** IaC is written and syntax-checked in CI, not deployed
  against a live subscription.
- **Auth:** AAD is implemented, feature-flagged off locally and on in Azure.
- **LLM provider:** OpenRouter accepted for this build; Azure OpenAI is the
  documented production alternative behind the same interface.
- **Live LLM cost:** live calls are opt-in and never part of `pytest` or the
  default smoke run. Free slugs get retired and throttled without notice, so a
  live demo wants a funded key and a paid model.

## Verification per phase

- `uv run pytest` (fake LLM) plus `uv run pytest -m live` for the real
  end-to-end path — both drive the graph, not just individual functions.
- `docker compose up`, walked through manually with the README's demo script
  before a phase counts as done.
- `az bicep build` on the templates as the Azure sanity check.
- The evaluation report reviewed for citation validity, latency and leg
  contribution before presentation prep.
