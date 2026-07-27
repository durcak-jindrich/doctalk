# Governance Checklist

Phase 10 of `PLAN.md`. A draft data-governance catalog entry (the brief's
"e.g., Collibra entry draft") plus a checklist of the controls a governance
review would ask about — what's built, what's a known gap. Not a
compliance sign-off; a starting point for one.

## Data asset entry

| Field | Value |
|---|---|
| Asset name | DocTalk document workspace |
| Description | Ad-hoc Q&A over a user-uploaded set of ≤5 internal documents (PDF/DOCX/MD); answers are grounded in and cited to that content only |
| Asset type | Application-managed dataset (Postgres tables `documents`, `chunks`) + derived vector index |
| Owner | Product/engineering team operating the deployment (case-study: candidate) |
| Data steward | Whoever administers the deployed instance — no in-app steward role exists |
| Source system | Files a user uploads through the UI/API; no upstream system-of-record integration |
| Classification | Internal — assumed non-public, content-agnostic (the system does not itself classify document sensitivity) |
| Sensitivity | Unclassified by the system; **the operator is responsible for not uploading regulated data** (PII/PHI/PCI) without a matching review, since DocTalk has no sensitivity detection ([`security-limitations.md`](security-limitations.md#data-handling)) |
| Lineage | Upload → parse → chunk → embed → store (Postgres) → retrieve → cite. One hop to a third party: retrieved chunk text is sent to the configured LLM provider (OpenRouter, or Azure OpenAI in-tenant) per question |
| Retention | Indefinite until explicitly deleted (`DELETE /api/documents/{id}`); no TTL or automatic expiry |
| Access control | Entra ID bearer-token authentication in Azure (`AUTH_ENABLED`), off locally; authentication only — every valid token sees the whole shared workspace, no per-document ACL |
| Related policies | None encoded in-app; this checklist and [`security-limitations.md`](security-limitations.md) are the closest equivalent today |

## Groundedness & answer-quality controls

The governance question a reviewer usually asks first — "can this system
make something up and present it as fact" — is answered structurally, not
by policy. Detail and rationale:
[`technical-decisions.md`](technical-decisions.md#grounded-synthesis--citation-governance).

| Control | Status |
|---|---|
| Answers restricted to uploaded content only, never general knowledge | Built — enforced by prompt + retrieval gate, not just instruction |
| Every claim carries a citation resolvable to a real, retrieved chunk | Built — deterministic validator, not model self-report |
| Explicit refusal when nothing relevant is retrieved | Built — pre-LLM gate on rerank score, spends no LLM call |
| Bounded correction instead of silent citation-stripping | Built — one retry, then withheld, never shown with the bad marker quietly removed |
| Citation proves the claim is *entailed*, not just *sourced* | **Gap** — validator proves resolution only; `docs/evaluation.md`'s substring faithfulness check is the closest signal today, and it only catches a wrong fact, not unsupported reasoning reaching a right one |
| Prompt-injection resistance from uploaded content | Best-effort — sources framed as data; real backstop is that injection still can't fabricate a valid citation ([`security-limitations.md`](security-limitations.md#injection-surfaces)) |

## Access & lifecycle controls

| Control | Status |
|---|---|
| Authentication on all data-access endpoints | Built in Azure (`AUTH_ENABLED=true`), off locally by design |
| Authorization scoped below "whole workspace" | **Not built** — single shared workspace has no per-user/per-tenant partition to scope to ([`security-limitations.md`](security-limitations.md#authn--authz)) |
| Explicit, auditable deletion | Built — cascading delete, immediate, no soft-delete/undo |
| Retention policy / automatic expiry | **Not built** — indefinite by default |
| Audit trail (who uploaded/asked/deleted what) | **Not built** — logs capture *what happened in the pipeline* (route, verdict, latency, cost), not *which caller* did it, since there is no per-user identity captured beyond the bearer token's own validation |
| Secrets never logged or committed | Built — `.env` gitignored locally; Key Vault + managed identity in Azure; logs are metadata-only, never document/question content |
| Third-party data transfer disclosed | Documented here and in `security-limitations.md` — chunk text leaves the deployment boundary to the LLM provider on every question |

## Open actions before a production governance sign-off

1. Assign a real data steward and a sensitivity classification process for
   uploaded content — today the system is classification-blind by design.
2. Decide a retention policy (auto-expire workspace after N days of
   inactivity?) instead of indefinite-until-manually-deleted.
3. Add per-user/per-tenant workspace partitioning if this ever serves more
   than one trust domain — the current single-workspace model is a
   deliberate case-study scope choice, not an oversight
   ([`technical-decisions.md`](technical-decisions.md#document-workspace-single-capped-set-not-a-growing-corpus)),
   but it is the first thing to revisit.
4. Add an audit log keyed on caller identity once `AUTH_ENABLED` is on by
   default, so "who uploaded/asked/deleted what" is answerable.
5. Decide whether entailment-level faithfulness checking (an LLM-judge pass,
   accepted quota cost) is worth adding beyond the current substring check —
   deferred in Phase 9 for a 7-case golden set;
   [`docs/evaluation.md`](evaluation.md) has the reasoning.
