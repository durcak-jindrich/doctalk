# DocTalk — Technical Decisions

Why each choice in `PLAN.md` was made — alternatives weighed, trade-offs
accepted. Kept terse by design: one entry per decision, rationale in a
line or two, not a narrative. This is the source for the presentation's
"approach / key decisions / trade-offs" section.

## Runtime: Python 3.12, not 3.14

System Python is 3.14.3; `sentence-transformers` (embeddings + reranker)
has no 3.14 wheel yet (checked 2026-07-25, supports up to 3.13). Pinned to
3.12 via `uv` (`cpython-3.12.11`, already local) to avoid a broken install
mid-Phase-1.

## Document workspace: single capped set, not a growing corpus

Brief's "1–5 documents" read as one bounded working set, not a
multi-session corpus. Hard cap of 5, enforced at upload (reject, no silent
eviction) — keeps the citation surface small/predictable and avoids
workspace/session scope the brief never asked for. Persisted in Postgres
so the set survives restarts (durability, not room to grow). The
content-addressed `document_id` would generalize to multi-workspace later;
out of scope now.

## Retrieval architecture: hybrid, fused, reranked

- Lexical-only (BM25) — cheap, deterministic, misses paraphrases.
- Dense-only (embeddings) — catches semantics, weak on exact IDs/names/numbers.
- **Hybrid (selected)** — RRF-fused dense + lexical, then cross-encoder
  reranked. Production strategy from day one — no "simple now, hybrid
  later" path, no full-context-stuffing option considered.

**Pipeline:** embed query locally → `pgvector` cosine search (dense) +
`pg_search` BM25 (lexical) → RRF fusion → cross-encoder rerank
(`cross-encoder/ms-marco-MiniLM-L-6-v2`) → top-N chunks (+ `chunk_id` +
metadata) to the LLM. One `Retriever` interface, one production
implementation (`HybridRerankRetriever`) — not a swappable tier.

## Storage: Postgres + pgvector, not in-memory

Hybrid retrieval is baseline, so persistence is baseline too.

- **One DB, two extensions:** `pgvector` (dense) + `pg_search`/ParadeDB
  (BM25), fused at query time. Local dev: `paradedb/paradedb` image.
- **Azure caveat:** `pg_search` may not be allow-listed on Azure Database
  for PostgreSQL Flexible Server → falls back to `tsvector`/`ts_rank` for
  the lexical leg there (still hybrid, weaker scorer). Confirm against
  current Azure docs before presenting as settled; see
  `docs/azure-deployment.md`.
- **Not FAISS:** no persistence, no managed Azure counterpart — fine for a
  purely ephemeral demo, not for this brief's durability bar.
- **Not Pinecone/Chroma/Qdrant:** cloud-only-outside-Azure, vector-only/no
  BM25, and no-Azure-managed-service, respectively. `pgvector` consolidates
  metadata + vectors + lexical in the one DB already needed, with a real
  Azure PaaS counterpart — not a claim it outperforms these at scale
  (irrelevant at this document count).

## Document & chunk identity (citation correctness)

- `document_id = slugify(filename_stem) + "-" + sha256(bytes)[:6]` —
  human-readable, content-addressed (re-upload of the same file = free
  de-dup).
- `chunk_id = f"{document_id}#c{chunk_index:04d}"` — stable, sortable,
  traces back to its document. Per-chunk metadata: `section_path` (heading
  hierarchy, where the format has one), `page_number` (PDF only),
  `char_start`/`char_end`, text.
- UI citations are human-readable first ("policy-handbook.pdf, p. 4" / "§
  2.3 Sick Leave"), `chunk_id` as an expandable secondary detail —
  satisfies "chunk ID or document name" while staying demo-appropriate.
- Citation validation is deterministic/code-level, not
  LLM-honesty-dependent (see `governance` node below).

## Chunking strategy: structure-aware, not fixed-size

- **Markdown:** heading hierarchy → `section_path`, split within a section
  on paragraph boundaries.
- **DOCX:** `python-docx` paragraph styles (Heading 1/2/3) → `section_path`,
  same paragraph split.
- **PDF:** per-page text (`pdfplumber`, keeps `page_number`), paragraphs
  via blank-line heuristic; PDFs often lack structure, so `section_path`
  may be null and citation falls back to page number.
- Within a section, paragraphs merge up to a **token-based** target
  (`tiktoken`, since both the embedding model and LLM are token-bound) with
  ~10–15% overlap. An oversized paragraph splits on sentence boundaries as
  a last resort — never mid-sentence.

## Phase 1 implementation notes

- **Cap checked before parse/embed** — a rejected upload costs one query,
  not a wasted embedding pass.
- **Dedup is a full no-op** — identical bytes → same `document_id`,
  returns the existing record untouched, doesn't recount against the cap.
- **Chunks never cross a section boundary**, even at the cost of a small
  trailing chunk — `section_path` accuracy matters more than packing
  efficiency for citations.
- **Overlap is token-tail, not character-tail** (decode the last N tokens
  via `tiktoken`) — predictable token cost, but the overlap span's
  `char_start` is an approximation (genuine chunk content offsets stay
  exact). Same approximation applies to the rare hard split of a
  single oversized sentence.
- **Embeddings are L2-normalized** so `pgvector`'s `vector_cosine_ops`
  index agrees with a plain dot product.
- **`EMBEDDING_DIM` is config, not derived** — the `chunks.embedding`
  column is a fixed-width `VECTOR(N)`. Swapping `EMBEDDING_MODEL` for a
  different output dimension needs the `chunks` table (or the whole
  `doctalk_pgdata` volume) dropped — no migration path, acceptable as a
  one-time dev-time operation.
- **`documents.char_count`** is summed extracted-text length (post-parse),
  a rough size indicator, not the raw file's byte size.

## Phase 2 implementation notes

- **`pg_search` syntax verified against the running container** (0.24.3),
  not the docs — the corpus mixes old/new syntax. Working form: `text @@@
  'query'` to match, `paradedb.score(id)` to rank, `id` as `key_field` is
  auto-untokenized (no cast needed).
- **`pgvector` query params need explicit wrapping:** a bare Python list
  in a raw `WHERE`/`ORDER BY` parameter fails (`vector <=> double
  precision[]`) — wrap with `pgvector.Vector(...)`. Not needed for
  inserts, where the driver infers the column type.
- **RRF constant `k=60`** (the standard default), `leg_top_k=20` per leg,
  reranked down to `top_k` — generous enough pool for the cross-encoder to
  correct fusion mistakes at this document count.
- **Rerank score doubles as a relevance signal:** an off-topic query
  scored ~-11 vs. +1.5/+8.9 for genuine matches in manual testing — Phase
  3's "nothing relevant, refuse" path can likely threshold on this rather
  than inventing a separate check.

## LLM provider — OpenRouter

Single OpenAI-SDK-compatible adapter behind an `LLMClient` interface;
model choice is one `.env` string (cheap/free during dev, stronger for the
demo). **Trade-off:** a third-party proxy — a production deployment with
genuinely sensitive documents should route through an in-tenant provider
(Azure OpenAI, same AAD/Key Vault boundary) instead; `LLMClient` stays
generic enough for that swap. Embeddings/reranker run locally
(`sentence-transformers`), decoupled from OpenRouter — no added retrieval
cost, works offline.

## Agentic orchestration (LangGraph)

Production `/ask` entry point, not an optional wrapper:
- **`retrieve`** — the hybrid + rerank pipeline above.
- **`synthesize`** — grounded LLM synthesis with citations.
- **`governance`** — deterministic citation validator: invalid citations or
  empty retrieval trigger one bounded retry, then explicit refusal — never
  a silent ungrounded pass-through. Confirms `chunk_id` was retrieved, not
  that it entails the claim — faithfulness is checked separately in
  evaluation (Phase 9).
- **`summarize`** — distinct tool for "summarize document X," reuses
  retrieval, still cited.

Retrieval/synthesis/validation are built and unit-tested as plain
functions first (Phase 2–3), wrapped as graph nodes later (Phase 4) — a
build-sequencing choice, not a sign the graph is optional.

## UI/UX approach

Demoed live to a non-technical Product Owner — real design care, not a
debug page:
- FastAPI serving static HTML/CSS/vanilla JS (no framework/build step, no
  CDN dependency at demo time).
- Drag/drop upload with remaining-slot indicator (1–5), file-type
  validation feedback, per-document remove (needed given the hard cap).
- Chat-style Q&A, citations as expandable chips, designed empty/error/
  "not found" states.
- Observability in the UI, not just logs: expandable "Under the hood"
  panel per answer — latency, token usage/cost, which retrieval leg
  contributed each cited chunk. Doubles as the "go deeper" moment.

## Azure readiness

Treated as a real deployment target:
- **Compute:** Azure Container Apps — fits a containerized
  FastAPI+LangGraph workload better than App Service, simpler than AKS here.
- **Database:** Azure Database for PostgreSQL Flexible Server + `pgvector`
  (`ts_rank` fallback for lexical — see Storage above).
- **Secrets:** Key Vault via Managed Identity; local `.env` fallback.
- **Auth:** Entra ID (AAD) JWT validation, feature-flagged
  (`AUTH_ENABLED=false` locally, `true` in Azure) — implemented, not stubbed.
- **Registry:** Azure Container Registry.
- **IaC:** Bicep for Container Apps Env + App, PostgreSQL Flexible Server,
  Key Vault, Managed Identity + RBAC, Container Registry; `az deployment
  group validate` as a dry-run check without a live deploy.
- **Docs/CI:** `docs/azure-deployment.md` (resource diagram, steps,
  `pg_search` caveat); example GitHub Actions workflow (build → push to
  ACR → deploy), documented without live secrets wired up.

## Local run — Docker Compose

`docker compose up` (`app` + `db` on `paradedb/paradedb`, named volume) is
the primary quickstart and the closest local mirror of the Azure Container
Apps + managed Postgres shape — "runs locally" and "Azure-ready" are the
same architecture. A non-Docker path (local venv + local Postgres) stays
documented in the README for faster dev-loop iteration.
