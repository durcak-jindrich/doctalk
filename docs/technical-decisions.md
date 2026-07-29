# Technical Decisions

One entry per decision: the choice, the reason, the trade-off accepted. Terse
by design — this is the source for the presentation's "key decisions /
trade-offs" section, not a design narrative. What the system *does* is in
[`README.md`](../README.md); code-level gotchas live in code comments.

## Scope & runtime

- **Python 3.12, not 3.14** — `sentence-transformers` has no 3.14 wheel
  (checked 2026-07-25). Pinned via `uv` to avoid a broken mid-build install.
- **One capped 5-document workspace, not a growing corpus.** The brief's "1–5
  documents" reads as a bounded working set: reject at the cap, no silent
  eviction, no multi-session library. Keeps the citation surface small.
  Postgres persistence is for surviving restarts, not for accumulating.

## Parsing, chunking & identity

- **Structure-aware chunking, not fixed-size.** Markdown headings and DOCX
  heading styles give `section_path`; PDFs give `page_number`. Chunks never
  cross a section boundary — `section_path` accuracy beats packing efficiency.
- **`target_tokens=240`, 12.5% overlap — set by the embedding window, not by
  taste.** `all-MiniLM-L6-v2` accepts 256 tokens and silently truncates past
  that; the original 400-token target left a third of each chunk citable but
  invisible to the dense leg. Widening means a wider-window embedder and a
  rebuilt `chunks` table.
- **Overlap is a token tail, not a character tail** — predictable token cost,
  at the price of an approximate `char_start` for the overlap span only.
- **Oversized paragraphs split on sentence boundaries**, never mid-sentence: a
  cited passage has to be readable on its own.
- **`document_id = slugify(stem)-sha256(bytes)[:6]`** — readable and
  content-addressed, so re-uploading a file is free de-dup. Keying on
  (filename, content) means the same file renamed takes a second slot;
  accepted, since the filename is part of how a user identifies a source.
- **`chunk_id = {document_id}#c{index:04d}`** — stable, sortable, traceable to
  its document. Per-chunk metadata: `section_path`, `page_number`,
  `char_start`/`char_end`. The UI leads with human-readable provenance
  ("handbook.pdf, p. 4") and keeps `chunk_id` as secondary detail.

## Storage & migrations

- **Postgres + `pgvector` + `pg_search`, not an in-memory index.** Hybrid
  retrieval is baseline, so persistence is baseline; one database holds
  metadata, vectors and the BM25 index. *Not FAISS* (no persistence, no
  managed Azure counterpart); *not Pinecone/Chroma/Qdrant* (cloud-only outside
  Azure, no BM25, no Azure PaaS respectively). Not a claim it wins at scale —
  irrelevant at 5 documents.
- **Azure caveat:** `pg_search` is not allow-listed on Azure Database for
  PostgreSQL; the intended fallback is a weaker `ts_rank` lexical leg. Not yet
  built — see [`azure-deployment.md`](azure-deployment.md).
- **Versioned SQL in `migrations/`, applied before the app starts** by a
  one-shot `migrate` Compose service — never lazily on first ingest, which
  would 500 whichever request hit a fresh database first.
- **The runner is psql on the database image, not Python on the app image.**
  Applying DDL needs a database client and nothing else. The previous version
  imported torch and loaded the embedder to create a schema (~8s warm, ~30s
  cold, blocked on the app build); now it is ~1s, runs in parallel with that
  build, and needs no API keys.
- **Applied migrations are tracked and checksummed.** `schema_migrations`
  records version + sha256; the run is one transaction holding
  `pg_advisory_xact_lock`, so replicas starting together serialise instead of
  racing and a mid-run failure leaves no half-applied schema. Editing an
  applied file is a hard error, not silent drift.
- **Alembic rejected** — its value is autogenerating diffs from SQLAlchemy
  models, which this project does not have.
- **Vector width is a literal (`VECTOR(384)`)**, because a migration must
  replay identically everywhere; deriving it from a loaded model made the
  schema depend on runtime state. `tests/unit/test_migrations.py` catches an
  `EMBEDDING_MODEL` width mismatch instead, at no runtime cost.
- **`app/storage/migrations.py` replays the same `.sql` files from Python** for
  `scripts/reset_db.py` and test fixtures, so a dev machine needs no psql. It
  only rebuilds from scratch; incremental application lives in the shell runner
  alone.

## Retrieval

- **Hybrid, fused, reranked** — lexical-only (BM25) misses paraphrases,
  dense-only is weak on exact IDs, names and numbers. RRF-fused `pgvector` +
  `pg_search` → cross-encoder rerank → top-N to the LLM. One production
  implementation, not a "simple now, hybrid later" tier.
- **RRF `k=60`, `leg_top_k=20` per leg** — a generous enough pool for the
  reranker to correct fusion mistakes at this document count.
- **The lexical leg uses `paradedb.match`, not `text @@@ '<query>'`** — the
  string form feeds raw input to ParadeDB's query parser, so ordinary
  punctuation in a question is read as query syntax and errors.

## Grounded synthesis & citation governance

Four independent gates enforce groundedness, because prompt wording is not a
control; the gates themselves are listed in
[`README.md`](../README.md#how-groundedness-is-enforced). Why they take this
shape:

- **Citations travel as source numbers (`[2]`), not chunk IDs.** The number →
  `chunk_id` mapping is rebuilt in code from the list actually sent, so a
  citation cannot name an unretrieved chunk and the fabrication surface is a
  small integer. Small models also reproduce `[2]` reliably and long IDs
  unreliably. A `chunk_id`-shaped marker is still accepted and folded back.
- **Validation proves resolution, not entailment** — that the cited chunk was
  retrieved and shown, not that it supports the claim. Faithfulness is measured
  separately in [`evaluation.md`](evaluation.md).
- **An uncited answer counts as ungrounded**, or "cite nothing" becomes a way
  around the validator.
- **The retrieval gate keys on score, not row count** — dense search always
  returns its top-k, so rows prove nothing about relevance.
- **`MIN_RERANK_SCORE = -5.0`, evaluated and kept.** The golden set separates
  answerable (≈ +2 to +9) from off-topic (≈ −11) with wide margin either side,
  so there is no evidence of the failure mode this value exists to prevent
  (false-refusing an answerable question). A narrower band (≈ −10.5 to −10.0)
  would score marginally better on the fixture; not acted on with 6 scored
  cases — [`evaluation.md`](evaluation.md) has the numbers.
- **Prose brackets are ignored, not flagged** — only digits and `chunk_id`
  tokens count as citation attempts, so "[sic]" can't trigger a false refusal.
  A hallucinated `[policy.pdf]` reads as prose and is caught by the
  at-least-one-citation rule instead.
- **`INSUFFICIENT_CONTEXT` anywhere in a reply is a refusal.** Accepted cost: a
  partial answer that appends the token is refused wholesale. Not observed in
  the golden-set run.
- **Per-attempt usage is accumulated** — a retry costs real tokens the
  observability panel would otherwise understate.
- **`LLMClient` is synchronous**, matching sync retrieval, so `/ask` runs as one
  blocking unit in FastAPI's threadpool.

## Agentic orchestration (LangGraph)

- **The graph is the production `/ask` pipeline, not an optional wrapper.**
  Nodes were built and unit-tested as plain functions first, then decomposed —
  build sequencing, not a sign the graph is bolted on.
- **The graph owns the governance loop; there is no second pipeline.** The
  retry once lived as a Python `for` loop in `synthesize()`; keeping both would
  mean two implementations of the groundedness policy that could drift. Cost:
  one more indirection to read, for a single place where an answer can reach a
  user.
- **The corrective retry is an edge, not an inner loop** — so it appears in the
  node path, and an answer that needed a correction is visibly distinct from one
  that didn't. `govern` enforces the attempt budget, bounding the cycle.
- **Refusals are written by nodes, not edges.** An edge cannot write state, and
  a refusal's *reason* is state the caller needs; so a node sets `answer` and
  the following edge only asks whether it is set.
- **Routing is a regex, not an LLM classifier.** Classifying every question
  with a model would spend quota per turn on a decision two patterns settle,
  and be non-deterministic to test. Cost: recall — unusual phrasing falls
  through to retrieval, which still answers. The safe direction to fail in.
- **The summarize tool selects sources structurally, not by relevance.** With
  no query to be relevant to, it takes each document's opening chunks under a
  budget split across the workspace: every document represented, prompt
  bounded. Only whole-workspace requests route here — "summarize the leave
  policy" names a topic and goes to retrieval. Summaries pass through the same
  `govern` node.

## LLM provider — OpenRouter

- **One OpenAI-SDK-compatible adapter behind `LLMClient`**, model configured as
  one `.env` string. **Trade-off:** a third-party proxy — genuinely sensitive
  documents belong on an in-tenant provider (Azure OpenAI, same AAD/Key Vault
  boundary), which this interface reduces to a constructor swap. Embeddings and
  reranking run locally, so retrieval costs nothing and works offline.
- **Free slugs churn and throttle** — the original default 404'd (retired) and
  its replacement 429'd on every call. Handled with a raised SDK retry budget
  and 404/429 mapped to typed errors that name the fix.

## API & UI

- **Sync `def` handlers, not `async def`.** Parsing, embedding, psycopg and the
  LLM call all block, so `async def` would block the event loop; sync handlers
  run in the threadpool instead. One execution model end to end.
- **A refusal is a 200.** "The documents don't answer this" is the product
  working. Only an unusable provider is a 503 — the one condition a user cannot
  fix by rephrasing.
- **Upload reports per file, not per request** — three files where one is an
  unreadable PDF ingests the other two and explains the third. Batch-failing
  would make a 5-document cap painful to work with.
- **Mutations return the whole workspace**, so the slot indicator cannot drift
  out of sync with what the server just did.
- **Models load at startup, the LLM client stays lazy.** The embedder and
  cross-encoder are warmed in the lifespan hook; the LLM client is not, because
  it needs an API key and the app must still boot and accept uploads without
  one.
- **Static HTML/CSS/vanilla JS, no build step and no CDN** — nothing to fail at
  demo time. Drag/drop upload with a slot indicator, citations as expandable
  chips, designed empty/error states. One light surface, pinned with
  `color-scheme`, so an OS in dark mode cannot half-restyle the form controls.
- **A refusal is styled as a calm, deliberate outcome**, not an error — it is
  the feature the brief is really asking for.
- **Document and model text is inserted as a text node, never as HTML.**
  Uploaded content is untrusted and the model echoes it back, so string-built
  markup would be an injection path straight from a file into the page.
- **Observability in the UI, not just the logs** — an expandable panel per
  answer, which doubles as the "go deeper" moment in the demo.

## Observability

- **Per-node timing, not one total** — a single latency number cannot say
  whether a slow answer was the model or the reranker. Retrieval is usually the
  surprise, not the LLM.
- **Instrumentation wraps nodes; it is not written into them.** `_instrument`
  times, logs and records every node identically, so a node body stays about its
  decision and none can be forgotten. Nodes report via `_detail`.
- **Steps are attached after the run** — a node cannot know its own duration
  until it returns, and none sees the whole run, so `answer_question` assembles
  the final `Answer`.
- **JSON Lines by default** — queryable as shipped (Azure Log Analytics ingests
  them without a parser); `LOG_FORMAT=text` for local reading. Every line
  carries a `trace_id`, also returned as `X-Trace-Id`; an inbound one is
  honoured, which is what lets a front door correlate across hops later.
- **Cost is recorded per attempt**, so a corrective retry cannot be
  under-reported.

## Testing & evaluation

- **The default loop spends no quota.** `addopts = -m 'not live'` enforces it
  rather than trusting habit; the fast suite fakes `LLMClient`, so it needs no
  key, stays deterministic, and cannot be broken by a provider.
- **Faking the model is what makes retrieval testable.** An obedient fake that
  cites whatever it is given removes the model as a variable, so a golden-set
  failure is a retrieval or governance failure and nothing else. Trade-off: a
  scripted refusal proves only plumbing.
- **Live tests exist for the claim that cannot be faked** — whether a real model
  declines a question the documents don't answer. They assert structure, never
  wording, so a model swap doesn't break them; failure modes a provider won't
  produce on demand (null content, 429s, retired slugs) are covered offline
  against a stubbed SDK.
- **Assertions are per-leg, not "either leg"** — a hybrid retriever that had
  quietly become dense-only would still satisfy `dense_rank or lexical_rank`.
- **The browser journey (`tests/e2e/`) is one test parametrized over fake and
  live**, not two suites. The default run costs nothing and proves the
  plumbing — each parser reaching the UI, citations that open the passage they
  point at, an off-topic question refused before any LLM call; `-m live` runs
  the identical journey where the answer must contain the fact and the model
  itself must decline the fabrication trap. Uvicorn runs in-process, so
  `answer_graph` can be overridden while Chromium drives the app over HTTP.
- **Its answer timeout is derived from the provider settings**, not picked: a
  free-tier draft that spends its retry budget can legitimately take two
  minutes, and a hardcoded guess turns that into a red test for a working
  system.
- **The evaluation reuses the graph and the golden set**, not a parallel scoring
  harness: same `answer_question` entry point, same cases the integration suite
  asserts on, so a metric describes production behaviour.
- **Faithfulness is a substring match against a fact named per case**
  (`expect_answer_contains`), not an LLM judge. Cheap, deterministic, honest
  about its limit — it catches a wrong number, not a right number reached by
  unsupported reasoning. An LLM judge would spend quota to buy a check that
  still isn't entailment.
- **Faithfulness and cost are only computed for `--live` runs.** Fake mode is
  real upstream of the model, but its reply is scripted, so scoring it would
  only prove the script matches itself. It still writes a marked report, so
  retrieval-side metrics can be re-checked for free after a retrieval change.
- **The `MIN_RERANK_SCORE` sweep scores the gate's own job**, not the final
  outcome: an in-domain-but-uncovered question is *supposed* to reach the model
  and be declined there, so a threshold that lets it through isn't penalized.

## Deployment

- **Docker Compose is the primary local path** (`app` + `paradedb/paradedb`,
  named volume) and the closest mirror of the Azure shape — "runs locally" and
  "Azure-ready" are the same architecture. A venv + local Postgres path stays
  documented for a faster dev loop.
- **Container Apps, not App Service or AKS** — fits a containerized
  FastAPI + LangGraph workload better than the former, simpler than the latter.
- **CPU-only torch wheels** (`pytorch-cpu` index, `sys_platform == 'linux'`).
  PyPI's Linux torch drags in the whole CUDA stack — cuDNN, NCCL, cuBLAS,
  Triton, ~3 GB — for a workload that embeds on CPU. Same version, no GPU
  runtime: the image went 5.26 GB → 1.43 GB. Costs one redundant-looking
  `torch` dependency, because `tool.uv.sources` is ignored for transitive ones.
- **Both models and the tiktoken table are baked into the image**, with
  `HF_HUB_OFFLINE=1`. A cold container used to download ~180 MB from
  huggingface.co inside the lifespan hook before it could serve; now it boots
  in ~9s with no egress beyond the LLM provider. Trade: +180 MB, and pointing
  `EMBEDDING_MODEL` at a model that was not baked in means unsetting the flag.
- **`.dockerignore` is an allowlist, not a denylist.** Four paths are opted in,
  everything else is excluded — which keeps `.venv/` (1 GB) and `.git/` out of
  the build context and `.env` unreachable from any `COPY` added later.
- **Azure Database for PostgreSQL Flexible Server + `pgvector`.** `pg_search`
  is not allow-listed there, so the current schema does not deploy clean;
  `infra/modules/postgres.bicep` says so rather than pretending otherwise.
- **Key Vault via managed identity, `.env` locally** — `AZURE_KEY_VAULT_URL`
  set → secrets load through `DefaultAzureCredential` before `Settings` is
  built.
- **Entra ID JWT validation is implemented, not stubbed** (`app/api/auth.py`),
  feature-flagged off locally and on in Azure.
- **Bicep IaC, hand-authored against the resource schemas.** CI's
  `az bicep build` is its only machine check; nothing has been deployed to a
  live subscription. Detail: [`azure-deployment.md`](azure-deployment.md).
