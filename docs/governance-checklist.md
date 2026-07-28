# Governance Checklist

A draft data-governance catalog entry (the brief's "Collibra entry draft") plus
the controls a governance review would ask about, and their real status. Not a
compliance sign-off — a starting point for one.

## Data asset entry

| Field | Value |
|---|---|
| Asset name | DocTalk document workspace |
| Description | Ad-hoc Q&A over a user-uploaded set of ≤5 internal documents (PDF/DOCX/MD); answers grounded in and cited to that content only |
| Asset type | Application-managed dataset (Postgres `documents`, `chunks`) + derived vector index |
| Owner | The team operating the deployment (case study: the candidate) |
| Data steward | Whoever administers the instance — no in-app steward role exists |
| Source system | Files uploaded through the UI/API; no upstream system-of-record integration |
| Classification | Internal — assumed non-public; the system does not classify document sensitivity itself |
| Sensitivity | Unclassified by the system. **The operator is responsible for not uploading regulated data** (PII/PHI/PCI) without a matching review — there is no sensitivity detection ([security](security-limitations.md#data-handling)) |
| Lineage | Upload → parse → chunk → embed → store (Postgres) → retrieve → cite. One external hop: retrieved chunk text goes to the configured LLM provider per question |
| Retention | Indefinite until explicitly deleted (`DELETE /api/documents/{id}`); no TTL |
| Access control | Entra ID bearer tokens in Azure (`AUTH_ENABLED`), off locally. Authentication only — every valid token sees the whole shared workspace |
| Related policies | None encoded in-app; this checklist and [`security-limitations.md`](security-limitations.md) are the closest equivalent |

## Groundedness & answer quality

The question a reviewer asks first — "can this system make something up and
present it as fact?" — is answered structurally, not by policy. Rationale:
[`technical-decisions.md`](technical-decisions.md#grounded-synthesis--citation-governance).

| Control | Status |
|---|---|
| Answers restricted to uploaded content, never general knowledge | **Built** — prompt plus a pre-LLM retrieval gate, not just instruction |
| Every claim carries a citation resolvable to a retrieved chunk | **Built** — deterministic validator, not model self-report |
| Explicit refusal when nothing relevant is retrieved | **Built** — gate on rerank score, spends no LLM call |
| Bounded correction instead of silent citation-stripping | **Built** — one retry, then withheld |
| Citation proves the claim is *entailed*, not just *sourced* | **Gap** — the validator proves resolution only; the substring faithfulness check in [`evaluation.md`](evaluation.md) is the closest signal, and it catches a wrong fact, not unsupported reasoning reaching a right one |
| Prompt-injection resistance from uploaded content | **Best-effort** — sources framed as data; the backstop is that injection still can't fabricate a valid citation ([security](security-limitations.md#injection-surfaces)) |

## Access & lifecycle

| Control | Status |
|---|---|
| Authentication on all data-access endpoints | **Built** in Azure (`AUTH_ENABLED=true`), off locally by design |
| Authorization scoped below "whole workspace" | **Not built** — a single shared workspace has no partition to scope to ([security](security-limitations.md#authn--authz)) |
| Explicit, immediate deletion | **Built** — cascading, no soft-delete or undo |
| Retention policy / automatic expiry | **Not built** — indefinite by default |
| Audit trail (who uploaded, asked, deleted) | **Not built** — logs capture what happened in the pipeline, not which caller did it |
| Secrets never logged or committed | **Built** — `.env` gitignored, Key Vault in Azure, logs metadata-only |
| Third-party data transfer disclosed | **Documented** — chunk text leaves the deployment boundary on every question |

## Open actions before a production sign-off

1. Assign a data steward and a sensitivity-classification process for uploaded
   content — the system is classification-blind by design.
2. Replace indefinite retention with a policy (auto-expire after N days idle?).
3. Add per-user/per-tenant workspace partitioning before this serves more than
   one trust domain. The single workspace is a deliberate scope choice, but it
   is the first thing to revisit.
4. Add an audit log keyed on caller identity once `AUTH_ENABLED` is the default.
5. Decide whether entailment-level faithfulness checking (an LLM-judge pass, at
   a quota cost) is worth adding beyond the current substring check — deferred
   for a 7-case golden set; [`evaluation.md`](evaluation.md) has the reasoning.
