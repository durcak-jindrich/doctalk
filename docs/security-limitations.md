# Security & Limitations

Phase 10 of `PLAN.md`. Consolidates the security posture scattered across
the codebase and other docs into one write-up — it links to, rather than
repeats, [`docs/technical-decisions.md`](technical-decisions.md) (groundedness
gates, citation design), [`docs/azure-deployment.md`](azure-deployment.md)
(Azure-specific gaps), and the README's
[Limitations](../README.md#limitations) section (product-level caveats).

**Scope.** DocTalk is a single-tenant interview case study: one shared
document workspace, no multi-tenant data separation, not hardened for
production traffic. What follows is what that implies, not a claim it's
production-hardened.

## Trust boundaries

- **Uploaded documents are untrusted input**, not just data. Content is
  parsed, embedded, and later echoed back into LLM prompts and the UI — a
  document is the one artifact an external party fully controls.
- **The question text is untrusted input.**
- **The LLM response is untrusted output** until the citation validator
  resolves every marker against chunks actually retrieved
  ([`technical-decisions.md`](technical-decisions.md#grounded-synthesis--citation-governance)).

## Injection surfaces

| Surface | Handling |
|---|---|
| SQL | Every query in `app/storage/repository.py` is parameterized (`%s` via psycopg); no string-built SQL anywhere in the codebase. |
| XSS | The frontend (`app/static/app.js`) inserts document/model/question text via `textContent`/DOM properties, never `innerHTML` — an uploaded document or model reply cannot inject markup. |
| Prompt injection | The synthesis prompt frames sources as numbered data, not instructions (`app/synthesis/prompts.py`); the smoke test suite includes a document that tries to override it. **Best-effort, not airtight** — the real containment is downstream: a successful injection still cannot manufacture a citation that resolves to a real chunk, so it can bias wording, not fabricate a source. |
| Path traversal | Filenames are stored and displayed as-is, never used to build a filesystem path — documents live in Postgres, not on disk, so an uploaded `../../etc/passwd` is just a string in a column. |

## Data handling

- **Storage:** parsed text and embeddings persist in Postgres (`documents`,
  `chunks`); nothing is written to disk outside the database. Deleting a
  document (`DELETE /api/documents/{id}`) cascades its chunks in the same
  transaction — no orphaned rows, no soft-delete/undo.
- **Retention:** indefinite until deleted. No TTL, no automatic expiry —
  matches the brief's single working-set model
  ([`technical-decisions.md`](technical-decisions.md#document-workspace-single-capped-set-not-a-growing-corpus)),
  not a data-lifecycle policy.
- **Logs are metadata-only.** Structured logs (`app/observability.py`)
  carry route, verdict, chunk counts, scores, token/cost counts, and
  timings — never question text, chunk text, or model output. A log line
  cannot leak document content even if logs end up somewhere less trusted
  than the database (e.g. Azure Log Analytics).
- **No PII detection/redaction.** Whatever a document or question contains
  flows through as-is; there is no scanning for sensitive data classes at
  ingestion.
- **Third-party exposure:** every `/api/ask` call sends the retrieved chunk
  text to OpenRouter (or whichever `LLMClient` backend is configured). This
  is a real boundary crossing for genuinely sensitive documents — the
  documented production mitigation is swapping in an in-tenant provider
  (Azure OpenAI) behind the same `LLMClient` interface, not a code change
  ([`technical-decisions.md`](technical-decisions.md#llm-provider--openrouter)).

## AuthN / AuthZ

- **Off by default locally, on in Azure** — `AUTH_ENABLED` gates Entra ID
  bearer-token validation (`app/api/auth.py`) on every `/api/*` route.
  `/health` and the static frontend stay open (load-balancer probes, no
  data in the JS/HTML itself).
- **Authentication, not authorization.** A valid token proves *who*, not
  *what they may see* — every valid token can read/write/delete the entire
  shared workspace. There is no per-document ACL and no scope/role check
  beyond `aud`/`iss`. This matches the single-workspace model; it stops
  being adequate the moment more than one tenant's documents share a
  deployment.
- **No rate limiting** on any endpoint — an authenticated (or, locally,
  anonymous) caller can call `/api/ask` as fast as the server accepts
  connections. A cost concern (LLM calls) before it's an availability one at
  this scale.
- **No CORS policy configured.** The frontend is served same-origin by the
  same FastAPI app that serves `/api/*`, so none is needed for the shipped
  UI; a separate frontend origin would need one added.

## Secrets

- **Local:** `.env`, gitignored, never committed. `.env.example` documents
  the shape with no real values.
- **Azure:** Key Vault via the Container App's user-assigned managed
  identity (`app/config.py`) — no client secret, no admin password stored
  anywhere reachable from the app. Detail:
  [`azure-deployment.md`](azure-deployment.md#secrets-and-identity).
- **Never logged.** `OPENROUTER_API_KEY`/`DATABASE_URL` are read into
  `Settings` and used directly; neither appears in the structured log
  schema above.

## Network & infrastructure

- **Local:** Docker Compose, no TLS — matches any local dev server; not a
  deployment target.
- **Azure:** TLS terminates at Container Apps' default ingress. **Not
  built:** VNet integration / private endpoint for Postgres (currently
  `AllowAzureServices`, open to all Azure-origin traffic), custom domain,
  WAF. Detail and rationale:
  [`azure-deployment.md`](azure-deployment.md#whats-not-implemented).

## Input validation

- File size capped at `max_upload_bytes` (10 MB default) — a memory bound
  (the whole file loads before parsing), enforced before parsing starts.
- Extension allow-list (`.pdf`/`.docx`/`.md`) enforced by
  `app/parsers/__init__.py`; anything else is rejected with a clear error,
  per-file, so one bad upload in a batch doesn't fail the rest.
- **No malware/antivirus scanning** of uploaded files — parsing errors
  (corrupt file, no text layer) surface as a rejection; a crafted malicious
  PDF/DOCX is only as safe as `pdfplumber`/`python-docx` themselves.
- **No OCR** — a scanned/image-only PDF fails ingestion explicitly rather
  than silently producing an empty, uncitable document.

## Out of scope for this case study

Not built, and not claimed to be: audit trail of who asked/uploaded what,
data classification/sensitivity labeling, backup/disaster-recovery policy,
DDoS protection, dependency/container vulnerability scanning in CI,
penetration testing. Each would be a real next step before production use
with genuinely sensitive documents; none is required to demonstrate the
brief's baseline or stretch goals.

## Product-level limitations

Groundedness, citation, and routing limitations (what the system gets
*wrong*, not *insecure*) live in the README's
[Limitations](../README.md#limitations) section — kept there rather than
duplicated here, since they're about answer quality, not the security
boundary.
