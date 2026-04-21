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

Each row maps one completed ticket to the test evidence that currently exists, flags whether a staging smoke is needed, notes what exists, and identifies the gap relative to `doc/operations/testing.md` §Per-ticket checklist. Tickets 0006–0009 are infra-only or read-only; the checklist items that apply are noted in the Gap column. Ticket 0010 is audited for Phase-0-complete behavior only (Phases 1–4 deferred/paused).

| Ticket | Feature / behavior introduced | Local test evidence (file + test func names, or `—` if none) | Staging smoke needed? (yes / no + reason) | Staging smoke exists? (file, or `—`) | Gap (what's missing to satisfy testing.md §Per-ticket checklist) |
|---|---|---|---|---|---|
| 0001 | JWT `exp` uses tz-aware datetime (python-jose requirement); inline comment documents the exception to the naive-UTC convention | `tests/test_auth_flow.py::test_register_login_me` — token is minted and accepted (implicitly exercises exp encoding). No dedicated unit test for the tz-aware convention itself. | no — doc-only change; no new route, secret, or external API | `—` | No dedicated test asserting the `exp` claim is tz-aware. The convention is documented by a comment, not a test. Gap: add a unit test in `tests/test_jwt_claims.py` (already exists) that decodes a fresh access token and asserts `exp` is an integer (UTC epoch), confirming encoding did not raise. Low priority given the implicit coverage from every auth flow test. |
| 0002 | pytest-asyncio `event_loop` deprecation fix — removed custom session-scoped `event_loop` fixture; `asyncio_mode = "auto"` confirmed in `pyproject.toml` | n/a — infra only (test-harness change; the "test" is that the suite runs without `DeprecationWarning`) | no — local tooling change only | `—` | Acceptable: the fix is self-proving (running `pytest` without the deprecation warning is the verification). No production behavior changed. No test gap. |
| 0003 | Replaced `passlib` with direct `bcrypt` calls in `auth_service.py`; `hash_password` / `verify_password` rewritten; passlib removed from deps | `tests/test_auth_flow.py::test_register_login_me`, `::test_password_reset_flow`, `::test_change_password_invalidates_old_session` — every password hash/verify path is exercised end-to-end through the auth flow tests. | no — library swap, no new route or external API | `—` | No dedicated unit test for `hash_password` / `verify_password` in isolation (e.g., assert `$2b$` prefix, assert wrong password returns False). The integration coverage via auth flow tests is strong but a focused service-layer test in `tests/services/` would be cleaner. Gap: add `tests/services/test_auth_service.py` with `test_hash_and_verify_password` and `test_wrong_password_returns_false`. Medium priority. |
| 0004 | Frontend scaffold — Next.js 16, TypeScript strict, Tailwind v4, React Query v5, `lib/api.ts` `apiFetch` helper | n/a — infra only (frontend scaffold; no backend logic changed; no backend test runner configured) | no — frontend scaffold; no new GCP secret, external API, or backend route | `—` | Acceptable per `doc/operations/testing.md` §Open gaps: "Frontend test runner not installed." Lint + typecheck + build are the current gate. No backend test gap. |
| 0005 | docker-compose stack (Postgres + backend + frontend); `/health` route added to backend; `.env.example` regenerated | `tests/test_auth_flow.py::test_health` — asserts `GET /health` returns 200 with `{"status": "ok"}`. | no — docker-compose is a local-dev artifact; `/health` is tested locally; staging had its own deploy path | `—` | Coverage is adequate for the `/health` route. The compose file itself is not tested (it's infrastructure). One minor gap: `test_health` does not assert the `"database": "connected"` key (only `status`). Low priority. |
| 0006 | Staging GCP foundation — project, IAM, Cloud SQL, Artifact Registry, Secret Manager (user-executed, no code changes) | n/a — infra only (gcloud commands; no backend code or test added) | yes — IAM bindings, secret presence, and Cloud SQL reachability are all staging-only concerns that cannot be reproduced locally | `—` | Infra-only ticket; local tests are not applicable. The gap is that no `smoke_healthz.py` exists to assert the staging environment is reachable post-setup. The staging smoke in ticket 0007's resolution (`curl /health → 200`) covered this manually but is not codified as a script. Gap: `scripts/smoke_healthz.py` (Phase 3 of this ticket). |
| 0007 | Staging first deploy — `cloudbuild.yaml`, Cloud Build trigger, `*.run.app` backend + frontend live (user-executed) | n/a — infra only (CI/CD pipeline; no backend production code changed beyond what was already tested) | yes — Cloud Build step ordering, Artifact Registry push, migration-before-deploy, Cloud Run env vars, and `/health` reachability are all staging-specific | `—` | Same gap as 0006: no codified `smoke_healthz.py`. The manual `curl /health` verification from the Resolution constitutes implicit staging smoke but is not a runnable script. Gap: `scripts/smoke_healthz.py`. |
| 0008 | Staging custom domains — Cloudflare CNAMEs + Cloud Run domain mappings for `staging.carddroper.com` and `api.staging.carddroper.com` (user-executed) | n/a — infra only (DNS + domain mapping; no backend code changed) | yes — DNS resolution, TLS cert, and Cloud Run domain binding are all staging-specific glue | `—` | Same gap as 0006/0007: no `smoke_healthz.py`. Additionally, CORS behavior on the custom domain has no smoke test. Gap: `scripts/smoke_healthz.py` would cover the reachability portion; CORS is harder to automate and lower priority. |
| 0009 | Code audit — read-only inventory of `backend/app/` and `frontend/`; findings triaged into follow-up tickets (0011, 0012); no production code changed | n/a — infra only (audit produces doc files only; no code or behavior changed) | no — read-only audit; no route, secret, or deployment change | `—` | Acceptable: this ticket produces `doc/audits/*.md` files. No test gap. |
| 0010 (Phase 0 only) | `email_service.py` reshaped: new async `send_email(template, to, dynamic_template_data)` with `EmailTemplate` enum, tenacity retry, `asyncio.to_thread` offload, singleton client, sandbox mode, structured logging, no-key fallback; 5 callsites in `routes/auth.py` updated to new signature with `try/except` best-effort semantics; `scripts/smoke_email.py` created; `requirements.txt` deleted; `Dockerfile` updated to `pip install .`; Settings fields added (SENDGRID_API_KEY as SecretStr, SENDGRID_SANDBOX, 5 template IDs, FRONTEND_BASE_URL) | `tests/services/test_email_service.py`: `test_happy_path_returns_message_id`, `test_sandbox_mode_sets_flag`, `test_no_key_fallback_no_client_call`, `test_secretstr_empty_falls_through`, `test_retry_on_503_succeeds_third_attempt`, `test_retry_on_connection_error`, `test_no_retry_on_400`, `test_missing_template_id_raises_value_error`, `test_event_loop_not_blocked`; `tests/test_auth_flow.py::test_register_succeeds_when_email_send_raises` (best-effort preservation) | yes — real SendGrid API key, DKIM authentication, template IDs in Secret Manager, and actual email delivery are staging-specific | `scripts/smoke_email.py` exists but **does not conform to the `testing.md` smoke pattern**: missing `SMOKE OK: email` success marker; uses `--to` / `--template` args rather than being self-contained and idempotent; does not use `smoke+` email prefix | Gap on `smoke_email.py`: (1) must print `SMOKE OK: email` on success per `testing.md` §Staging tier; (2) should use a `smoke+<uuid>@<domain>` recipient or at minimum document the pattern; (3) Phase 4 (real staging send + DKIM verification) is paused pending this ticket. No local test gap — all 9 service tests + 1 callsite test are present. |
| 0011 | Global 500 exception handler in `main.py` (returns `INTERNAL_ERROR` JSON, logs with `request_id`); JWT `iss`/`aud` claims added to all token mints and decode validations; `JWT_ISSUER` / `JWT_AUDIENCE` Settings fields added | `tests/test_exception_handler.py`: `test_handler_returns_correct_json_shape`, `test_handler_redacts_exception_details`, `test_handler_logs_unhandled_exception_event`, `test_exception_handler_registered_in_app`, `test_unhandled_exception_smoke_returns_500`; `tests/test_jwt_claims.py`: `test_access_token_has_iss_and_aud`, `test_access_token_accepted_by_me`, `test_wrong_audience_returns_401`, `test_wrong_issuer_returns_401`, `test_missing_aud_returns_401`, `test_missing_iss_returns_401` | yes — the JWT iss/aud rejection path and the 500 handler shape in a real Cloud Run deployment are confirmed via the staging smoke in the ticket Resolution (five curl checks all passed) | `—` (no script; staging smoke was manual curl commands in the Resolution) | Local coverage is thorough. The only gap is the lack of a codified `smoke_auth.py` staging script — the manual JWT wrong-audience/wrong-issuer curls from the Resolution are not repeatable as a script. Gap: `scripts/smoke_auth.py` (Phase 3 of this ticket). The `test_health` assertion gap from 0005 (missing `"database": "connected"` assertion) is still present. |

---

### Prioritized gap list

**Phase 2 candidates (local tests to add):**

- 0001 — no test asserts that a freshly minted access token carries an integer `exp` (tz-aware encoding implicit check) — `tests/test_jwt_claims.py` (add `test_exp_is_integer_epoch`)
- 0003 — no isolated unit tests for `hash_password` / `verify_password`; covered only through HTTP flow — `tests/services/test_auth_service.py` (new file; add `test_hash_and_verify_password`, `test_wrong_password_returns_false`, `test_hash_produces_bcrypt_prefix`)
- 0005 — `test_health` does not assert `"database": "connected"` key — `tests/test_auth_flow.py` (one-line assertion addition to existing `test_health`)

**Phase 3 candidates (smoke scripts to add):**

- healthz — no script exercises `GET /health` against staging; needed as post-deploy sanity check for 0006/0007/0008 — `scripts/smoke_healthz.py`
- auth — no script runs register → verify → login → refresh → logout against staging; needed to confirm the JWT iss/aud, cookie, and DB-backed flows work end-to-end on real infrastructure — `scripts/smoke_auth.py`
- email (fix existing) — `scripts/smoke_email.py` exists but is missing `SMOKE OK: email` success marker and `smoke+` email prefix convention required by `testing.md` §Staging tier — fix in-place as part of Phase 3

---

### Drift found

**`smoke_email.py` does not conform to the `testing.md` smoke-script pattern.** `testing.md` §Staging tier requires each smoke script to: (a) print `SMOKE OK: <feature>` on success, (b) use `smoke+` prefixed email addresses so a nightly sweep can reap them, and (c) exit non-zero with a clear message on any assertion failure. `scripts/smoke_email.py` prints `sg_message_id=<id>` (no `SMOKE OK:` marker), takes a `--to` argument with no enforced `smoke+` prefix, and only exits 1 on argument parsing errors — not on a `send_email` exception (the exception would propagate and produce a Python traceback rather than a clean failure message). This is not a production-code bug, but it means the smoke script cannot be plugged into the suite defined by `testing.md` without modification. Flagged for repair in Phase 3.

**`test_register_login_me` asserts `r.status_code == 200` for the register endpoint.** The `test_register_succeeds_when_email_send_raises` test includes a comment noting the register endpoint returns 200, not 201, despite ticket 0010's acceptance saying "assert the HTTP response is still 201." This is a test comment documenting a known deviation, not a test failure. The production endpoint itself returns 200 (AuthResponse). No action needed unless the endpoint contract is intentionally changed to 201 in a future ticket; flag here for orchestrator awareness.

## Resolution

*Added by orchestrator on close.*
