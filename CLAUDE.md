# DocTalk — working conventions

Grounded Q&A over 1–5 uploaded documents (PDF/DOCX/MD), with citations.
Interview case study. **Read [`README.md`](README.md) first** — it owns the
architecture, stack, configuration and limitations; this file is only the
rules for working in the repo.

- Brief: [`docs/assignment.md`](docs/assignment.md) — if anything here
  conflicts with the brief, the brief wins.
- Build order and phase status: [`PLAN.md`](PLAN.md).
- Why anything is the way it is: [`docs/technical-decisions.md`](docs/technical-decisions.md).

## Commands

| | |
|---|---|
| Install | `uv sync` |
| Run (Docker, app + ParadeDB) | `docker compose up --build` |
| Run (API only, needs Postgres) | `uv run uvicorn app.main:app --reload` |
| Test — never calls an LLM (`tests/integration/` needs `docker compose up -d db`) | `uv run pytest` |
| Test live LLM — opt-in, spends quota | `uv run pytest -m live` |
| Lint / format | `uv run ruff check .` / `uv run ruff format .` |

Python is pinned to 3.12 (`sentence-transformers` has no 3.14 wheel).

## Non-negotiable rules

- **Groundedness is the product, not a feature.** Never fabricate a citation.
  If nothing relevant was retrieved, refuse explicitly — never answer from
  general knowledge, never guess, never strip a bad citation and show the
  draft anyway. Getting this right beats adding capability on a shaky base.
- **LLM calls cost quota — spend them deliberately.** The default dev/test
  loop makes zero: use the fake `LLMClient`. Live calls only behind an
  explicit opt-in (`pytest -m live`, `--live` on a script) and only when a
  real model's behaviour is the thing being checked. Never poll or retry a
  live model to explore its behaviour.
- **Secrets stay out of git** — `.env` only (gitignored), Key Vault in Azure.
- **After each phase**, test the output, sanity-check the assumptions, and
  self-review against the brief's rubric before moving on.

## Code conventions

- Match the surrounding code: sync handlers end to end (parsing, psycopg and
  the LLM call all block; FastAPI's threadpool handles concurrency), typed
  dataclasses over dicts at module boundaries, `ruff` clean.
- **`app/graph/` owns control flow.** `app/synthesis/` holds primitives only
  (prompt, citation validator, refusal vocabulary) — do not reintroduce a
  second answering pipeline there.
- **Uploaded text and model output are untrusted** — parameterized SQL,
  `textContent` never `innerHTML`, sources framed as data in the prompt.
- **Schema changes are new numbered files in `migrations/`**, never lazy DDL
  from application code. Applied files are checksummed; editing one is an
  error.
- **Logs carry metadata only** — never question, chunk or answer text.
- Tests: unit and integration run against the fake `LLMClient`;
  `tests/live/` asserts structure, never wording, so a model swap can't break
  it. `tests/golden.py` is the single definition of a correct answer, shared
  with `scripts/evaluate.py`.

## Documentation rules

**Concise is a hard requirement, not a style preference** — an unread README
fails the brief. Docs must be complete *and* short enough to read end to end.

- One entry per decision: choice, why, trade-off — 1–3 lines. Not a narrative,
  not a record of how you got there.
- Say it once. `README.md` gets the headline and a link;
  `docs/technical-decisions.md` holds the reasoning. Routine implementation
  detail belongs in a code comment.
- Edit existing entries; never append per-phase sections. Deleting stale prose
  is part of finishing a phase.
- Any non-obvious design decision goes in the docs, not just in chat — the
  interview will reference them.
- `docs/evaluation.md` is **generated** by `scripts/evaluate.py`, and
  `samples/*.docx`/`*.pdf` by `scripts/make_samples.py`. Change the generator,
  never the output.
