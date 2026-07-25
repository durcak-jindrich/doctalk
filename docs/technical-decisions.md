# DocTalk — Technical Decisions

Why each choice in `PLAN.md` was made: alternatives weighed, trade-offs
accepted. Pull this in when you need the reasoning — `PLAN.md` stays the
lean, execution-focused reference.

## Runtime: Python 3.12, not 3.14

The system Python is 3.14.3, but `sentence-transformers` (embeddings +
cross-encoder reranker, both load-bearing in the retrieval design above) has
no Python 3.14 wheel as of this build (verified 2026-07-25 — supports up to
3.13). Pinned to 3.12 instead, via `uv` (already available locally as
`cpython-3.12.11`, no download needed) to avoid a broken `pip install`
partway through Phase 1.

## Document workspace: single capped set, not a growing corpus

The brief specifies "1–5 documents," read here as one bounded working set
rather than a multi-session corpus that accumulates over time. A hard cap
of 5 keeps the citation surface small and predictable for the demo, and
avoids scope creep into workspace/session management the brief never asked
for. The API enforces the cap — uploads beyond it are rejected until a
document is removed, no silent eviction. Storage still persists (see
below) so the one working set survives a restart; that's durability, not
an invitation to grow the set indefinitely. A multi-workspace/session model
is a plausible later extension (the content-addressed `document_id` scheme
would generalize), but it's out of scope here.

## Retrieval architecture

Three approaches were weighed:
1. **Lexical-only (BM25/full-text)** — deterministic and cheap, but misses
   paraphrased/semantic matches.
2. **Dense-only (embeddings)** — catches semantic matches, but weak on
   exact terms, IDs, names, and numbers that internal documents (policy
   numbers, product names, section refs) are full of.
3. **Hybrid dense + lexical, fused, then reranked** *(selected)* —
   combines both signals via Reciprocal Rank Fusion (RRF), then a
   cross-encoder reranks the fused candidates before anything reaches the
   LLM. This is the production strategy from day one; there's no "simple
   retriever now, hybrid later" path, and full-context-stuffing was never
   on the table.

**Pipeline:** embed the query locally (`sentence-transformers`) → cosine
search in `pgvector` for dense candidates → BM25 search over the same
chunks for lexical candidates → RRF fusion → cross-encoder rerank
(`cross-encoder/ms-marco-MiniLM-L-6-v2`) of the fused top-K → top-N chunks
(with `chunk_id` + metadata) passed to the LLM. A `Retriever` interface
exists for testability, but there's one production implementation —
`HybridRerankRetriever`, not a swappable tier.

## Storage: Postgres + pgvector, not in-memory

Hybrid retrieval is baseline, so the vector store is baseline too, and
must persist across restarts.

- **Single database, two extensions:** `pgvector` (dense) +
  `pg_search`/ParadeDB (native BM25 full-text) in one Postgres instance —
  one store for metadata, embeddings, and lexical search, fused at query
  time. Local dev uses the `paradedb/paradedb` Docker image.
- **Azure caveat:** Azure Database for PostgreSQL Flexible Server supports
  `pgvector`, but `pg_search`/ParadeDB may not be Azure-allow-listed —
  falls back to built-in `tsvector`/`ts_rank` for the lexical leg on Azure
  (still hybrid, weaker scorer). Needs confirming against current Azure
  docs; called out in `docs/azure-deployment.md`.
- **Why not FAISS:** simpler (in-process, no server/schema/migrations),
  and would be the right default for a purely ephemeral, local-only demo.
  Dropped because persistence across restarts is mandatory here and FAISS
  has no managed Azure counterpart — shipping index files yourself isn't
  "Azure-ready."
- **Why not Pinecone/Chroma/Qdrant:** Pinecone is cloud-only and outside
  Azure — a second third-party data flow on top of the OpenRouter one
  already accepted. Chroma is vector-only, no native BM25 leg, no managed
  Azure service. Qdrant supports hybrid search natively but has no
  Azure-managed offering either, and would mean running a second stateful
  service alongside the Postgres already needed for metadata. `pgvector`
  consolidates metadata, vectors, and lexical search into the one database
  already required, with a genuine Azure PaaS counterpart — not a claim it
  out-performs these at scale, which is irrelevant at this document count.

## Document & chunk identity (citation correctness)

Citations are the core requirement, so IDs need to be stable,
deterministic, and traceable end to end:
- `document_id = slugify(filename_stem) + "-" + sha256(file_bytes)[:6]` —
  human-readable and content-addressed (re-uploading the same file gives
  free de-duplication).
- `chunk_id = f"{document_id}#c{chunk_index:04d}"` — stable, sortable,
  trivially parsed back to its parent document. Metadata per chunk:
  `section_path` (heading hierarchy where the format has structure),
  `page_number` (PDF only), `char_start`/`char_end`, chunk text.
- Citations shown in the UI are human-readable first (e.g.
  "policy-handbook.pdf, p. 4" or "§ 2.3 Sick Leave"), with the precise
  `chunk_id` as an expandable secondary detail — satisfies "chunk ID or
  document name" while staying demo-appropriate for a non-technical
  audience.
- Citation validation is deterministic and code-level, not
  LLM-honesty-dependent (see the `governance` node below).

## Chunking strategy

The naive fixed-size char chunker was replaced with a structure-aware
strategy:
- **Markdown:** parse heading hierarchy first (building `section_path`),
  then split within a section on paragraph boundaries.
- **DOCX:** use `python-docx` paragraph styles (Heading 1/2/3) for
  `section_path`, same paragraph-boundary split.
- **PDF:** extract per-page text (`pdfplumber`, keeping `page_number`),
  detect paragraphs via blank-line heuristics; PDFs often lack clean
  structure, so `section_path` may be null and citation falls back to page
  number.
- Within a section, paragraphs are merged up to a **token-based** target
  size (via `tiktoken`, since both the embedding model and LLM are
  token-bound) with ~10–15% overlap between adjacent chunks. An oversized
  single paragraph splits on sentence boundaries as a last resort — never
  mid-sentence.

## LLM provider — OpenRouter

A single OpenAI-SDK-compatible adapter behind an `LLMClient` interface;
model selection is one `.env` string. Cheap/free model during dev and
testing, a stronger model as the demo default.

**Trade-off:** OpenRouter is a third-party proxy between the app and the
model. For genuinely internal/sensitive documents, a production
deployment should route directly through an in-tenant provider (e.g.
Azure OpenAI, under the same AAD/Key Vault boundary). OpenRouter is the
pragmatic choice for this build's cost/flexibility goals, not a
production recommendation — the `LLMClient` interface stays generic
enough for a direct Azure OpenAI swap later. Embeddings and the reranker
run locally (`sentence-transformers`), decoupled from the OpenRouter
call — no added API cost for retrieval, works offline.

## Agentic orchestration (LangGraph)

LangGraph orchestrates the tool nodes as the production `/ask` entry
point, not an optional wrapper:
- **`retrieve`** — the hybrid + rerank pipeline above.
- **`synthesize`** — grounded LLM synthesis with citations.
- **`governance`** — the deterministic citation validator as a graph
  node: invalid citations or nothing relevant retrieved trigger one
  bounded retry (adjusted query), then an explicit refusal — never a
  silent ungrounded pass-through. This check only confirms the `chunk_id`
  was actually retrieved, not that the chunk *entails* the claim —
  faithfulness is checked separately in evaluation (Phase 9).
- **`summarize`** — a distinct tool for "summarize document X" requests,
  reusing retrieval, still cited.

Core retrieval/synthesis/validation logic is built and unit-tested as
plain callable functions first (Phase 2–3), then wrapped as LangGraph
nodes (Phase 4) — a build-sequencing choice, not a sign the graph is
optional.

## UI/UX approach

Demoed live to a non-technical Product Owner, so the frontend gets real
design care rather than a debug page: clear affordances, responsive
feedback, accessible contrast/focus states, designed loading/error states.
- FastAPI serving static HTML/CSS/vanilla JS (no frontend framework/build
  step; CSS vendored, no CDN dependency at demo time).
- Drag/drop upload with a remaining-slot indicator (1–5 files), file-type
  validation feedback, per-document remove action (needed since the
  workspace is hard-capped — see above).
- Chat-style Q&A with citations as expandable chips (human-readable source
  + location), clearly designed empty/error/"not found" states.
- Observability surfaced in the UI, not just logs: each answer has an
  expandable "Under the hood" panel — latency breakdown, token usage/cost,
  which retrieval leg contributed each cited chunk. Satisfies the
  observability requirement and doubles as the "go deeper" moment in the
  presentation.

## Azure readiness

Treated as a real deployment target, not a documentation exercise:
- **Compute:** Azure Container Apps — better fit for a containerized
  FastAPI + LangGraph/background workload than App Service, simpler than
  AKS at this scale.
- **Database:** Azure Database for PostgreSQL Flexible Server with
  `pgvector` (native `ts_rank` fallback for lexical — see Storage above).
- **Secrets:** Azure Key Vault via Managed Identity +
  `azure-identity`/`azure-keyvault-secrets`; local `.env` fallback with no
  managed identity present.
- **Auth:** Entra ID (AAD) JWT bearer validation on the API,
  feature-flagged (`AUTH_ENABLED=false` locally, `true` in Azure) —
  implemented, not stubbed.
- **Registry:** Azure Container Registry for the built image.
- **IaC:** Bicep modules for Container Apps Environment + Container App,
  PostgreSQL Flexible Server, Key Vault, Managed Identity + RBAC,
  Container Registry — reviewable IaC files, not just prose. `az
  deployment group validate` used as a dry-run sanity check even without
  a live deploy.
- **Docs:** `docs/azure-deployment.md` with a resource diagram, concrete
  deployment steps, and the `pg_search`/Azure-extension caveat.
- **CI/CD:** example GitHub Actions workflow (build → push to ACR →
  deploy to Container Apps), documented even without live secrets wired
  up.

## Local run — Docker Compose

`docker compose up` (an `app` service + a `db` service on
`paradedb/paradedb`, named volume) is the primary quickstart, and the
closest local mirror of the Azure Container Apps + managed Postgres
shape — so "runs locally" and "Azure-ready" are the same architecture. A
non-Docker path (local venv + local Postgres) stays documented in the
README for faster dev-loop iteration, but Compose is the
guaranteed-to-work path for the demo and for anyone running the project
cold.
