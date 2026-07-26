# DocTalk — Technical Decisions

One entry per decision: the choice, the reason, the trade-off accepted.
Terse by design — this is the source for the presentation's "key decisions /
trade-offs" section, not a design narrative. Code-level gotchas live in code
comments, not here.

## Runtime: Python 3.12, not 3.14

`sentence-transformers` has no 3.14 wheel (checked 2026-07-25). Pinned via
`uv` to avoid a broken install mid-build.

## Document workspace: single capped set, not a growing corpus

The brief's "1–5 documents" read as one bounded working set. Hard cap of 5,
enforced at upload (reject, no silent eviction), persisted in Postgres for
durability across restarts — not room to grow. Keeps the citation surface
small and avoids multi-session scope the brief never asked for.

## Retrieval: hybrid, fused, reranked

Lexical-only (BM25) misses paraphrases; dense-only is weak on exact
IDs/names/numbers. **Selected:** RRF-fused dense (`pgvector`) + lexical
(`pg_search` BM25) → cross-encoder rerank → top-N chunks to the LLM. One
production implementation, not a swappable "simple now, hybrid later" tier.

- RRF `k=60`, `leg_top_k=20` per leg — a generous enough pool for the
  reranker to correct fusion mistakes at this document count.
- The lexical leg uses `paradedb.match`, not `text @@@ '<query>'`: the string
  form feeds raw input to ParadeDB's query parser, so ordinary punctuation in
  a question is read as query syntax and errors.

## Storage: Postgres + pgvector, not in-memory

Hybrid retrieval is baseline, so persistence is baseline. One DB, two
extensions: `pgvector` (dense) + `pg_search`/ParadeDB (BM25), fused at query
time.

- **Not FAISS:** no persistence, no managed Azure counterpart.
- **Not Pinecone/Chroma/Qdrant:** cloud-only-outside-Azure, no BM25, and no
  Azure managed service, respectively. `pgvector` consolidates metadata +
  vectors + lexical in the one DB already needed, with a real Azure PaaS
  counterpart — not a claim it wins at scale (irrelevant at 5 documents).
- **Azure caveat:** `pg_search` may not be allow-listed on Azure Database for
  PostgreSQL → lexical leg falls back to `ts_rank` there (still hybrid,
  weaker scorer). Confirm against current Azure docs before presenting as
  settled.

## Document & chunk identity

- `document_id = slugify(filename_stem)-sha256(bytes)[:6]` — readable and
  content-addressed, so re-uploading a file is free de-dup. Keying on
  (filename, content) means the same file renamed takes a second slot;
  accepted, since filename is part of how a user identifies a source.
- `chunk_id = {document_id}#c{index:04d}` — stable, sortable, traceable to
  its document. Metadata per chunk: `section_path`, `page_number` (PDF),
  `char_start`/`char_end`.
- UI cites human-readable provenance first ("handbook.pdf, p. 4"), with
  `chunk_id` as secondary detail.

## Chunking: structure-aware, not fixed-size

Markdown headings and DOCX heading styles give `section_path`; PDFs give
`page_number` (structure is usually absent). Within a section, paragraphs
merge to a token target with overlap; an oversized paragraph splits on
sentence boundaries, never mid-sentence. Chunks never cross a section
boundary — `section_path` accuracy matters more than packing efficiency.

- **`target_tokens=240`, 12.5% overlap, set by the embedding model's window,
  not by taste.** `all-MiniLM-L6-v2` accepts 256 tokens and silently
  truncates beyond that; the original 400-token target left a third of each
  chunk citable but invisible to the dense leg. Widening it means a
  wider-window embedder and rebuilding the `chunks` table.
- **Overlap is a token tail, not a character tail** — predictable token cost,
  at the price of an approximate `char_start` for the overlap span only.

## Schema migrations: versioned SQL, applied by the database image

Numbered files in `migrations/`, applied by `migrations/apply.sh` as a
one-shot `migrate` Compose service — not lazily on first ingest. Schema
readiness is a deploy-time guarantee rather than a side effect of whichever
request arrives first (a fresh DB hit by ask/list/delete would otherwise 500).

**The runner is psql on the ParadeDB image, not Python on the app image.**
Applying DDL needs a database client and nothing else. The previous version
ran in the application container, so bootstrapping the schema imported torch
and loaded the embedding model — ~8s warm, ~30s cold — and could not start
until the app image had finished building. Now it is ~1s and runs in parallel
with that build. It also means no API keys are passed to the migration step.

**Applied migrations are tracked and checksummed.** `schema_migrations` records
version and sha256; the whole run is one transaction holding
`pg_advisory_xact_lock`, so replicas starting together serialise rather than
race, and a mid-run failure leaves no half-applied schema. Editing an applied
file is a hard error, not silent drift. Alembic rejected: its value is
autogenerating diffs from SQLAlchemy models, which this project does not have.

**Vector width is a literal in the migration** (`VECTOR(384)`), because a
migration must replay identically on every database — deriving it from a
loaded model made the schema depend on runtime state. The mismatch this
guards against (`EMBEDDING_MODEL` swapped for a different width) is caught by
`tests/unit/test_migrations.py` instead, at no runtime cost. Changing embedder
is then a new migration rather than a volume wipe.

`app/storage/migrations.py` replays the same `.sql` files from Python for
`scripts/reset_db.py` and the test fixtures, so a dev machine needs no psql.
It only ever rebuilds from scratch; incremental application lives in the
shell runner alone.

## Grounded synthesis & citation governance

Groundedness is enforced by four independent gates, because prompt wording
is not a control:

1. **Retrieval gate (pre-LLM):** empty retrieval, or best rerank score below
   `MIN_RERANK_SCORE`, refuses without spending a call. Dense search always
   returns its top-k, so the *score*, not the presence of rows, is the signal.
2. **Prompt:** numbered sources, cite-every-claim, `INSUFFICIENT_CONTEXT`
   token for gaps, sources framed as data rather than instruction.
3. **Deterministic validation:** every marker resolved in code against the
   exact chunks sent to the model.
4. **Bounded retry, then refusal:** one corrective retry, then the draft is
   withheld — never strip-and-pass, which would leave an uncited claim on
   screen looking grounded.

Key choices:

- **Citations travel as source numbers (`[2]`), not chunk IDs.** The number →
  `chunk_id` mapping is rebuilt in code from the list actually sent, so a
  citation cannot name an unretrieved chunk; the fabrication surface is a
  small integer. Small models also reproduce `[2]` reliably and long IDs
  unreliably. A `chunk_id`-shaped marker is still accepted and folded back.
- **Validation proves resolution, not entailment** — that the cited chunk was
  retrieved and shown, not that it supports the claim. Faithfulness is
  measured separately in Phase 9.
- **An uncited answer counts as ungrounded**, or "don't cite anything" becomes
  a way around the validator.
- **`MIN_RERANK_SCORE = -5.0` is provisional**, picked off observed
  separation: covered questions score ≈ +7 to +9, in-domain-but-uncovered
  ≈ −6, nonsense ≈ −11. Re-tuned in Phase 9, where false refusal is the
  failure mode to watch.
- **Prose brackets are ignored, not flagged** — only digit and `chunk_id`
  tokens count as citation attempts, so "[sic]" can't trigger a false
  refusal. A hallucinated `[policy.pdf]` then reads as prose, and is caught
  instead by the at-least-one-citation rule.
- **`INSUFFICIENT_CONTEXT` anywhere in a reply is a refusal.** Accepted cost:
  a partial answer that appends the token is refused wholesale. Revisit if
  Phase 9 shows partial answers being lost.
- **Per-attempt usage is accumulated**, since a retry costs real tokens that
  the observability panel would otherwise understate.
- **`LLMClient` is synchronous**, matching sync retrieval, so `/ask` runs as
  one blocking unit in FastAPI's threadpool.

## LLM provider — OpenRouter

One OpenAI-SDK-compatible adapter behind `LLMClient`; model is one `.env`
string. **Trade-off:** a third-party proxy — production with genuinely
sensitive documents should route through an in-tenant provider (Azure
OpenAI, same AAD/Key Vault boundary), which `LLMClient` keeps to a
constructor swap. Embeddings and reranking run locally, so retrieval costs
nothing and works offline.

**Free slugs churn and throttle:** the original default 404'd (retired) and a
replacement 429'd on every call. Handled with a raised SDK retry budget and
404/429 mapped to typed errors naming the fix.

**Live calls are opt-in, and exactly one test makes one.** A suite that hits a
paid, rate-limited, non-deterministic third party on every run is slow,
flaky, and quietly expensive — so the default suite uses a fake `LLMClient`
and `pytest` is configured to deselect `live`-marked tests. But mocking
everything would never catch a retired model slug or a changed response
shape, so one `live` test stays, run deliberately (`pytest -m live`) when the
OpenRouter integration is the thing being checked. It asserts structure, not
wording, so a model change doesn't break it; the failure modes a real
provider won't produce on demand are covered offline against a stubbed SDK.

## Agentic orchestration (LangGraph)

Production `/ask` entry point, not an optional wrapper:
`route` → (`retrieve` | `gather_summary_sources`) → `draft` → `govern`.
Built and unit-tested as plain functions first (Phases 2–3), decomposed into
nodes in Phase 4 — build sequencing, not a sign the graph is optional.

**The graph owns the governance loop; there is no second pipeline.** Phase 3's
`synthesize()` held the retry as a Python `for` loop. Keeping both would mean
two implementations of the groundedness policy that could drift, so the loop
moved into the graph and `app/synthesis/` was reduced to primitives (prompt,
validator, refusal vocabulary). Trade-off: one more indirection to read, in
exchange for a single place where an answer can reach a user.

**The corrective retry is a graph edge, not an inner loop.** As an edge it
appears in the run's node path, so an answer that needed a correction is
visibly distinct from one that did not — which is what the observability panel
and the Phase 9 evaluation report on. `govern` enforces the attempt budget, so
the cycle is bounded.

**Refusals are written by nodes, not by edges.** An edge function cannot write
state, and a refusal's *reason* is state the caller needs; so a node that
decides to stop sets `answer`, and the edge after it only asks whether
`answer` is set. Both source nodes can end a run before any LLM call.

**Routing is a regex, not an LLM classifier.** Classifying every question with
a model would spend quota per turn on a decision two patterns settle, and
would be non-deterministic to test. Cost: recall — an unusual phrasing of
"summarize" falls through to retrieval, which still answers. That is the safe
direction to fail in.

**The summarize tool selects sources structurally, not by relevance.** For
"summarize the documents" there is no query to be relevant to, so it takes
each document's opening chunks under a budget split across the workspace —
every document represented, prompt bounded. Only whole-workspace requests
route here; "summarize the leave policy" names a topic and goes to retrieval.
Summaries pass through the same `govern` node, so they are cited or refused
like any other answer.

## API design

**Sync `def` handlers, not `async def`.** Parsing, embedding, psycopg and the
LLM call are all blocking, so `async def` would block the event loop; sync
handlers run in FastAPI's threadpool instead. One execution model end to end,
no concurrency lost.

**A refusal is a 200.** "The documents don't answer this" is the product
working, and the client renders it as an answer with `refused: true`. Only an
unusable provider is a 5xx (503), because that is the one condition the user
cannot fix by rephrasing.

**Upload reports per file, not per request.** Three files where one is an
unreadable PDF ingests the other two and says what went wrong with the third.
Batch-fails would make the 5-document cap needlessly painful to work with.

**Mutations return the whole workspace.** Upload and delete respond with the
document list and remaining capacity, so the slot indicator cannot drift out
of sync with what the server just did.

**Models load at startup, the LLM client stays lazy.** The embedder and
cross-encoder are warmed in the lifespan hook so the first question doesn't
pay for them. The LLM client is *not* built there: it needs an API key, and
the app must still boot and accept uploads without one.

## UI/UX approach

Demoed live to a non-technical Product Owner, so: real design care, not a
debug page. FastAPI serving static HTML/CSS/vanilla JS (no build step, no CDN
dependency at demo time). Drag/drop upload with a remaining-slot indicator,
chat-style Q&A with citations as expandable chips, designed empty/error
states, light and dark.

**A refusal is styled as a calm, deliberate outcome** — not an error. It is
the feature the brief is really asking for, so it must not look like a
failure.

**Everything from a document or a model is inserted as a text node**, never as
HTML. Uploaded content is untrusted and the model echoes it back into answers
and source previews, so string-built markup would be an injection path
straight from an uploaded file into the page.

**Observability in the UI, not just logs** — an expandable "under the hood"
panel per answer: route, latency, tokens/cost, and the graph path with a
per-node timing bar, verdict, and a repeated node marked so a corrective retry
is visible. It doubles as the "go deeper" moment in the demo.

## Observability

**Per-node timing, not one total.** A single latency number cannot say whether
a slow answer was the model or the reranker. Each node records its own
duration and verdict, so the panel and the evaluation both attribute cost to a
stage — retrieval is usually the surprise, not the LLM.

**Instrumentation wraps the nodes; it is not written into them.** `_instrument`
times, logs and records every node identically, so a node body stays about its
decision and no node can be forgotten. Nodes report what they decided by
returning `_detail`, which the wrapper lifts onto the step record.

**Steps are attached after the run, not inside a node.** A node cannot know its
own duration until it has returned, and none can see the whole run — so
`answer_question` assembles the final `Answer` with the complete step list and
wall-clock total.

**JSON Lines by default.** Logs are queryable as shipped (Azure Log Analytics
ingests them without a parser); `LOG_FORMAT=text` gives readable console output
locally. Every line carries a `trace_id`, also returned as `X-Trace-Id` and
echoed in the answer payload, so a question about one answer leads straight to
its log lines. An inbound `X-Trace-Id` is honoured, which is what lets a front
door correlate across hops later.

**Cost is recorded per attempt, not per answer.** A corrective retry is a real
LLM call and is counted, so the observability panel and the evaluation cannot
under-report what governance costs.

## Azure readiness

- **Compute:** Container Apps — fits a containerized FastAPI+LangGraph
  workload better than App Service, simpler than AKS.
- **Database:** Azure Database for PostgreSQL Flexible Server + `pgvector`
  (`ts_rank` lexical fallback, see Storage).
- **Secrets:** Key Vault via Managed Identity; local `.env` fallback.
- **Auth:** Entra ID JWT validation, feature-flagged off locally, on in Azure
  — implemented, not stubbed.
- **IaC:** Bicep for Container Apps, Postgres, Key Vault, Managed Identity +
  RBAC, ACR; validated with `az deployment group validate`, not deployed
  against a live subscription.

## Local run — Docker Compose

`docker compose up` (`app` + `paradedb/paradedb`, named volume) is the
primary quickstart and the closest local mirror of the Azure shape — "runs
locally" and "Azure-ready" are the same architecture. A venv + local Postgres
path stays documented for a faster dev loop.
