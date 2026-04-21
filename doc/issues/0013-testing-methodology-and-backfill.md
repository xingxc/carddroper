---
id: 0013
title: testing methodology doc + coverage audit + backfill
status: in_progress
priority: high
found_by: orchestrator 2026-04-21
---

## Context

We have three environments (local, staging, prod) defined in
`doc/operations/environments.md` but no written policy for what kind of testing
belongs where. Coverage across completed tickets is implicit — test files
exist but there is no audit of which features are covered at which tier, and
no curated staging smoke suite beyond the single `scripts/smoke_email.py`
created for ticket 0010.

This ticket makes the methodology explicit, audits what we already have,
backfills the gaps, and installs the expectation in the orchestrator
dispatch template so future tickets can't close without meeting it.

**Ticket 0010 Phase 4 (staging smoke) is paused** until this ticket lands the
smoke-script pattern so `smoke_email.py` slots into a suite instead of being
a one-off. 0010 resumes at this ticket's Phase 5.

Grounded in:
- `doc/operations/testing.md` (drafted in this ticket's Phase 0)
- `doc/operations/environments.md` (existing)
- `doc/issues/README.md` §Workflow (ticket template)
- `CLAUDE.md` (orchestrator + dispatch template)

## Acceptance

1. **Methodology doc exists.** `doc/operations/testing.md` drafted and linked
   from `doc/README.md`. Covers the three tiers, the core rule, per-tier
   scope, the smoke-script pattern, per-ticket checklist, backfill policy,
   agent dispatch expectations, and open gaps.

2. **Coverage audit.** A table of every completed ticket (0001–0011) mapped
   to: (a) what local test files cover it, (b) whether a staging smoke exists
   or is needed, (c) any gap. Lives in this ticket under §Audit results when
   Phase 1 completes. Agent produces the audit; orchestrator reviews.

3. **Local test backfill.** For any gap identified in §Audit results that
   can be closed with a local test, the test is written. Target: no
   completed ticket's core behavior is untested at the local tier.

4. **Staging smoke suite.** One `scripts/smoke_*.py` per feature area that
   touches infrastructure glue. Minimum set at closure:
   - `scripts/smoke_email.py` (exists from 0010 Phase 0 — validate it matches
     the pattern in `doc/operations/testing.md` §Staging tier)
   - `scripts/smoke_auth.py` (register → verify → login → refresh → logout end-to-end
     against staging URL)
   - `scripts/smoke_healthz.py` (basic availability — `GET /healthz` returns 200;
     also useful as a post-deploy sanity check)

   Each script is idempotent, self-cleaning, uses `smoke+` email prefixes,
   prints `SMOKE OK: <feature>` on success, exits non-zero with a clear
   message on failure.

5. **CLAUDE.md updated.** The dispatch brief template includes the "Testing
   requirements" block from `doc/operations/testing.md` §Agent dispatch
   expectations. Agents reading CLAUDE.md going forward inherit the rule.

6. **Doc index updated.** `doc/README.md` links to `doc/operations/testing.md`
   under the Operations section.

7. **0010 Phase 4 resumes.** After Phases 1–6 land, this ticket's Phase 5
   re-dispatches the 0010 staging smoke using the now-established pattern
   (run `smoke_email.py` against staging, validate `SMOKE OK: email` output).
   0010 flips to resolved on successful smoke.

## Phases

- **Phase 0 — Methodology doc (orchestrator, this turn).** `doc/operations/testing.md`
  drafted; `doc/README.md` and `doc/issues/README.md` updated. 0010 paused in its file.

- **Phase 1 — Coverage audit (dispatch backend-builder).** Agent inventories
  `backend/tests/` against completed tickets (0001–0011) and produces a
  gap table. Does NOT write new tests in this phase. Output lands in this
  ticket's §Audit results section.

- **Phase 2 — Local test backfill (dispatch backend-builder).** Based on
  §Audit results, fill gaps. One pass; agent reports pytest summary +
  files added.

- **Phase 3 — Staging smoke suite (dispatch backend-builder).** Write the
  three smoke scripts listed in Acceptance item 4. Each runnable against
  `https://api.staging.carddroper.com` with no GCP CLI dependency.

- **Phase 4 — CLAUDE.md dispatch template (orchestrator).** Insert the
  Testing requirements block. Update `doc/README.md` index.

- **Phase 5 — 0010 Phase 4 resume (user runs smoke, orchestrator closes
  0010).** Run `.venv/bin/python scripts/smoke_email.py` against staging,
  confirm `SMOKE OK: email`. Orchestrator flips 0010 to resolved.

## Verification

**Automated checks:**
- `.venv/bin/pytest` — green, with test count >= pre-ticket count.
- `.venv/bin/ruff check .` + `.venv/bin/ruff format --check .` — clean.
- Each smoke script: `.venv/bin/python -m py_compile scripts/smoke_*.py` — no syntax errors.

**Functional smoke (user-run, post-merge-to-main):**
- After Cloud Build deploys staging with this ticket's changes:
  - `.venv/bin/python scripts/smoke_healthz.py` → `SMOKE OK: healthz`
  - `.venv/bin/python scripts/smoke_email.py` → `SMOKE OK: email` (resolves 0010)
  - `.venv/bin/python scripts/smoke_auth.py` → `SMOKE OK: auth`
- Each exits 0. Non-zero exit with a clear failure message counts as a
  smoke failure — fix before promoting to prod.

## Out of scope

- **Frontend test runner setup.** Separate ticket (0014 or later) — Playwright
  vs. Vitest + RTL decision lands when the first real UI arrives (ticket 0011's
  verify/reset pages). This ticket explicitly flags the gap in
  `doc/operations/testing.md` §Open gaps and stops there.
- **CI pytest step.** Adding a test step to `cloudbuild.yaml` is noted as a
  gap, not fixed here. Future ticket.
- **Coverage reporting.** `pytest --cov` is not added. Future ticket if the
  suite grows enough to justify it.
- **Synthetic prod canaries.** Deferred until paying users.
- **Retroactively flipping ticket statuses.** We do not reopen 0001–0009 even
  if the audit finds gaps. We backfill coverage and move on.

## Report

Each dispatched phase reports:
- Files touched / added (paths + short description).
- For Phase 1: the audit table, raw, no edits.
- For Phases 2–3: pytest summary line, ruff summary, new test / script count.
- Deviations from the brief with justification.
- Any gap surfaced that cannot be closed in this ticket's scope, flagged for
  a follow-up ticket.

## Audit results

*Populated at Phase 1 completion by backend-builder. Leave empty until then.*

## Resolution

*Added by orchestrator on close.*
