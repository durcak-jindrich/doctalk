# Security & Limitations

The security posture in one place. Answer-quality limitations (what the system
gets *wrong*, not *insecure*) live in
[`README.md`](../README.md#assumptions--limitations); Azure-specific gaps in
[`azure-deployment.md`](azure-deployment.md#not-implemented).

**Scope.** DocTalk is a single-tenant case study: one shared document
workspace, no multi-tenant separation, not hardened for production traffic.
What follows is what that implies — not a claim it is production-ready.

## Trust boundaries

- **Uploaded documents are untrusted input**, not just data — a document is the
  one artifact an external party fully controls, and its content is parsed,
  embedded, and echoed back into prompts and the UI.
- **The question text is untrusted input.**
- **The LLM response is untrusted output** until the citation validator
  resolves every marker against chunks actually retrieved.

## Injection surfaces

| Surface | Handling |
|---|---|
| SQL | Every query in `app/storage/repository.py` is parameterized (`%s` via psycopg); no string-built SQL anywhere |
| XSS | `app/static/app.js` inserts document, model and question text via `textContent`/DOM properties, never `innerHTML` |
| Prompt injection | `app/synthesis/prompt.py` frames sources as numbered data, not instructions, and the smoke-test corpus includes a document that tries to override it. **Best-effort, not airtight** — the real containment is downstream: an injection can bias wording, but still cannot manufacture a citation that resolves to a retrieved chunk |
| Path traversal | Filenames are stored and displayed as-is, never used to build a filesystem path — documents live in Postgres, so an uploaded `../../etc/passwd` is just a string in a column |

## Data handling

- **Storage** — parsed text and embeddings live in Postgres (`documents`,
  `chunks`); nothing is written to disk outside the database. Deleting a
  document cascades its chunks in the same transaction: no orphans, no
  soft-delete, no undo.
- **Retention** — indefinite until deleted. No TTL, no automatic expiry; this
  matches the single working-set model, it is not a data-lifecycle policy.
- **Logs are metadata-only** (`app/observability.py`): route, verdict, chunk
  counts, scores, tokens, cost, timings — never question text, chunk text or
  model output. A log line cannot leak document content even if logs end up
  somewhere less trusted than the database.
- **No PII detection or redaction** — whatever a document or question contains
  flows through as-is; there is no scanning for sensitive data classes.
- **Third-party exposure** — every `/api/ask` sends retrieved chunk text to
  OpenRouter. A real boundary crossing for sensitive documents; the documented
  mitigation is swapping in an in-tenant provider (Azure OpenAI) behind the same
  `LLMClient` interface, which is a constructor change, not a rewrite.

## AuthN / AuthZ

- **Off locally, on in Azure** — `AUTH_ENABLED` gates Entra ID bearer-token
  validation (`app/api/auth.py`) on every `/api/*` route. `/health` and the
  static frontend stay open (probe traffic; no data in the HTML/JS itself).
- **Authentication, not authorization.** A valid token proves *who*, not *what
  they may see*: every valid token can read, write and delete the whole shared
  workspace. No per-document ACL, no scope or role check beyond `aud`/`iss`.
  Adequate for one workspace; inadequate the moment two tenants share a
  deployment.
- **No rate limiting** on any endpoint — a cost concern (LLM calls) before it is
  an availability one at this scale.
- **No CORS policy** — the frontend is served same-origin by the same app, so
  none is needed; a separate frontend origin would need one.

## Secrets

- **Local** — `.env`, gitignored, never committed. `.env.example` carries only
  the variables with no sensible default, with no real values; `app/config.py`
  is the authoritative list of tunables and defaults.
- **Azure** — Key Vault via the Container App's user-assigned managed identity
  (`app/config.py`): no client secret, no admin password reachable from the app.
  Detail: [`azure-deployment.md`](azure-deployment.md#secrets-and-identity).
- **Never logged** — `OPENROUTER_API_KEY`/`DATABASE_URL` are read into
  `Settings` and used directly; neither appears in the log schema above.

## Input validation

- **Size cap** — `MAX_UPLOAD_BYTES` (10 MB default), enforced before parsing
  starts, because the whole file loads into memory.
- **Extension allow-list** — `.pdf`/`.docx`/`.md` (`app/parsers/__init__.py`);
  anything else is rejected per-file, so one bad upload doesn't fail a batch.
- **No malware scanning** — parsing errors surface as a rejection, but a crafted
  malicious PDF/DOCX is only as safe as `pdfplumber`/`python-docx` themselves.
- **No OCR** — a scanned, image-only PDF fails ingestion explicitly rather than
  silently becoming an empty, uncitable document.

## Network

- **Local** — Docker Compose, no TLS; a dev server, not a deployment target.
- **Azure** — TLS terminates at Container Apps' default ingress. VNet
  integration, private endpoints, custom domain and WAF are
  [not built](azure-deployment.md#not-implemented).

## Out of scope for this case study

Not built, and not claimed: audit trail of who asked or uploaded what, data
classification and sensitivity labeling, backup/DR policy, DDoS protection,
dependency and container vulnerability scanning in CI, penetration testing.
Each is a real step before production use with sensitive documents; none is
needed to demonstrate the brief.
