---
id: 0014
title: pre-0015 cleanup — backend + frontend hygiene batch from audit 2026-04-21
status: resolved
priority: medium
found_by: pre-0015 audits 2026-04-21
---

## Context

Two full audits ran 2026-04-21 before kicking off the email-verification flow
(which is now ticket 0015):

- `doc/audits/2026-04-21-backend-pre-0014-audit.md`
- `doc/audits/2026-04-21-frontend-pre-0014-audit.md`

(The filenames say "pre-0014" because the email-verification flow was
originally going to be 0014. It's now 0015; this cleanup ticket took 0014
to land first. The audits are unchanged.)

Both audits issued a conditional green-light for the verification flow and
flagged a tight set of tactical fixes that do **not** depend on a consumer
being in place. This ticket lands those tactical fixes. The architectural
frontend items (401-refresh interceptor, auth context + `useAuth`, middleware
+ route groups) are intentionally deferred to 0015 — they need a real authed
page to validate against.

This ticket is batched across backend + frontend because the items are small,
independent, and benefit from landing before 0015 touches the same files.

## Acceptance

### Phase 0 — Backend (dispatch backend-builder)

Each item is a discrete change, all in one phase for one dispatch.

1. **F-1 (backend audit) — ruff-format the migration file.** Run
   `.venv/bin/ruff format alembic/versions/ee2ded47d8da_initial_schema.py`.
   Commit the reflow with no behavior change. After this,
   `.venv/bin/ruff format --check .` must be clean so the gate can be
   tightened in CI later.

2. **F-2 (backend audit) — delete dead `FRONTEND_URL` config field.**
   `FRONTEND_URL` in `app/config.py` is never read; all email-link
   construction uses `FRONTEND_BASE_URL` (spec is in the audit). Remove the
   field from `Settings` and its line from `backend/.env.example`. No
   other call-site change.

3. **F-4 (backend audit) — wire `require_not_locked` per auth.md.** The
   dependency exists in `app/dependencies.py` but is applied to zero routes.
   `systems/auth.md` §Soft cap specifies the 7-day lock applies to every
   route **except** `/auth/verify-email`, `/auth/resend-verification`,
   `/auth/change-email`, `/auth/me`, `/auth/logout`. Apply the dependency
   accordingly. Add 2 tests in `tests/test_auth_flow.py` (or a new file —
   your call): (a) a >7-day unverified user gets 403 from a locked route,
   (b) the same user still gets 200 from `/auth/me`. Verify the contract
   against `systems/auth.md` before wiring — if anything is ambiguous, flag
   it in the report rather than guessing.

4. **F-5 (backend audit) — fix public-endpoint status codes.** On
   `POST /auth/verify-email`, `POST /auth/reset-password`, and
   `GET /auth/validate-reset-token`, token-decode failures currently raise
   `unauthorized(...)` (401). Change these to `validation_error(...)` (422)
   — 401 on a public cookie-less endpoint is semantically wrong and will
   confuse frontend "invalid link" handling in 0015. Update any existing
   tests that asserted 401 to assert 422. Do NOT change the already-verified
   200 response, the not-found 404, or the 429 rate-limit response.

5. **F-7 (backend audit) — clean up the two pytest warnings.**
   - Add `asyncio_default_fixture_loop_scope = "function"` to
     `[tool.pytest.ini_options]` in `pyproject.toml`.
   - Fix the `test_exception_handler_registered_in_app` warning: either
     remove the module-level `pytestmark = pytest.mark.asyncio` and mark
     each async test individually, or override the mark on the sync test.
     Use whichever is cleaner.
   After this, `.venv/bin/pytest` must run with zero warnings.

**Out of scope for Phase 0:**
- F-3 (backend audit — `users.updated_at` best-effort) — informational
  only; deferred.
- F-6 (backend audit — `sleep 3` in cloudbuild.yaml) — deferred until we
  observe an actual intermittent failure.
- Any new feature work. This is cleanup.

### Phase 1 — Frontend (dispatch frontend-builder)

Independent of Phase 0. Can run in parallel.

1. **F-8 (frontend audit) — empty-string env guard.** `frontend/lib/api.ts`
   line 1: change `??` to `||` so an empty-string `NEXT_PUBLIC_API_BASE_URL`
   (from a missing `--build-arg` in Cloud Build) falls back to the local
   default instead of silently producing relative-path fetches. One-character
   fix; verify the built bundle by inspecting `.next/` for the baked URL
   after `npm run build`.

2. **F-3 (frontend audit) — wrap `fetch()` in network-error catch.** In
   `frontend/lib/api.ts`, wrap the `fetch()` call in `try/catch`. On a
   native `TypeError` (connection refused, DNS failure, CORS preflight
   rejection), re-throw as `new ApiError({ status: 0, code: "NETWORK_ERROR",
   message: "Network error — check your connection." })` so downstream
   `instanceof ApiError` checks work. Keep the existing non-OK response
   path unchanged.

3. **F-4 (frontend audit) — QueryClient defaults.** In
   `frontend/app/providers.tsx`, add to `makeQueryClient`:
   - `defaultOptions.queries.retry: 1`
   - `defaultOptions.queries.refetchOnWindowFocus: false`
   - `defaultOptions.mutations.retry: 0`
   These are the right defaults for an auth'd app where `/auth/me` will
   return 401 for logged-out users — we don't want 3 retries with
   exponential backoff on that.

4. **F-5 (frontend audit) — delete `public/next.svg`.** Framework logo;
   not imported anywhere. Missed by 0012's SVG purge. Confirm no imports
   via grep before deletion.

**Out of scope for Phase 1:**
- F-1 (frontend audit — 401-silent-refresh interceptor) — belongs in 0015
  because it needs the auth-context token store to integrate with.
- F-2 (frontend audit — auth context / `useAuth`) — 0015.
- F-7 (frontend audit — middleware + route groups) — 0015.
- F-6 (frontend audit — 204 cast) — to be handled naturally when 0015
  adds `api.get / api.post / api.delete` typed wrappers.
- F-9 (frontend audit — `.dockerignore` `*.md` nit) — no action needed.

## Verification

**Automated checks:**
- `.venv/bin/pytest` — 36+ passing, zero warnings.
- `.venv/bin/ruff check .` — clean.
- `.venv/bin/ruff format --check .` — clean (will now be clean after F-1).
- `npm run lint` — zero.
- `npx tsc --noEmit` — zero.
- `npm run build` — succeeds.
- `docker build ./backend` + `docker build ./frontend` — both succeed.

**Functional smoke (user, post-merge-to-main):**
- After Cloud Build deploys:
  - `.venv/bin/python scripts/smoke_healthz.py` → `SMOKE OK: healthz`
  - `.venv/bin/python scripts/smoke_auth.py` → `SMOKE OK: auth`
    (confirms the status-code changes and the `require_not_locked`
    wiring do not break the golden path)
  - `curl -sSf https://api.staging.carddroper.com/health` → 200 JSON

## Out of scope

- **Email verification signed-token flow + verify/reset frontend pages.**
  That's ticket 0015.
- **`require_not_locked` application to billing routes.** Billing doesn't
  exist yet; wire during Phase 6.
- **Cloud Build migrate-step robustness.** Separate ticket when the
  `sleep 3` actually bites us.
- **`noUncheckedIndexedAccess` / `noImplicitOverride` tsconfig tightening.**
  Still deferred from 0009 F-6.

## Report

Backend-builder (Phase 0):
- Files touched with a one-line what-changed for each.
- Test additions for F-4 (file + test-name list).
- pytest summary line (count + warnings).
- ruff check + ruff format summary.
- Any contract ambiguity surfaced when cross-checking auth.md for F-4.
- Any deviation from the brief.

Frontend-builder (Phase 1):
- Files touched.
- Confirmation of grep-before-delete for `next.svg`.
- lint / typecheck / build summary.
- Any deviation.

## Resolution

Resolved 2026-04-21. Two phases + two follow-on fixes.

**Phase 0 — Backend (backend-builder, commit 4fc4d33).** Five items:
- Ruff-formatted the sole alembic migration so `ruff format --check .` is now clean.
- Removed dead `FRONTEND_URL` from `app/config.py` + `.env.example`.
- Wired `require_not_locked` to `PUT /auth/password` (the only authenticated non-exempt route today). Interpreted auth.md §Soft cap "every route except [5]" as "every authenticated route" — documented as a spec ambiguity; fixed in auth.md (commit 888e0c9).
- Public-endpoint token-decode failures: `POST /auth/verify-email` and `POST /auth/reset-password` now return 422 instead of 401 on malformed tokens. The verify-email "user not found after successful decode" branch stays at 401 — edge case, revisit if it surfaces. `GET /auth/validate-reset-token` never returned 401 in the first place (ticket brief was wrong about its shape — it returns 200 with `{valid: False}`).
- pytest warnings eliminated: added `asyncio_default_fixture_loop_scope = "function"` to `pyproject.toml`; restructured `test_exception_handler.py` to stop leaking the async mark onto a sync test. 38 passing, 0 warnings.
- 2 new tests covering the locked-account path: 403 on `PUT /auth/password`, 200 still on `GET /auth/me`.

**Phase 1 — Frontend (frontend-builder, commit 4fc4d33).** Four items:
- `lib/api.ts` line 1: `??` → `||` so empty-string `NEXT_PUBLIC_API_BASE_URL` (missing `--build-arg`) falls back to localhost instead of producing silent relative-path fetches.
- `lib/api.ts`: `fetch()` wrapped in `try/catch`; native `TypeError` re-thrown as `ApiError(0, "NETWORK_ERROR", …)` so `instanceof ApiError` checks hold during outages. Agent correctly used the positional constructor (ticket brief sketched an object form that wouldn't have typechecked).
- `providers.tsx`: `queries.retry = 1`, `refetchOnWindowFocus = false`, `mutations.retry = 0`. Correct defaults for the `/auth/me` query 0015 will add.
- Deleted `public/next.svg` (framework logo, unimported). Closed the SVG purge started by 0012.

**Phase 2 — auth.md tightening (orchestrator, commit 888e0c9).** §Soft cap now says "every **authenticated** route" with an explicit note that anonymous endpoints are out of scope. Resolves the spec ambiguity the backend agent flagged.

**Phase 3 — Cloud Build hotfix (orchestrator, commit a8f1915).** Deleting the last SVG in Phase 1 left `frontend/public/` empty; git doesn't track empty directories so the folder vanished from the repo, and the frontend Dockerfile's `COPY /app/public` failed at Cloud Build with `stat app/public: file does not exist`. Added a 0-byte `frontend/public/.gitkeep` to preserve the directory. Postmortem lesson: when deleting the last file from a build-context directory, check the Dockerfile for hardcoded `COPY <dir>` references — worth adding to the per-ticket checklist.

**Staging smoke (user, 2026-04-21):** All three green — `smoke_healthz`, `smoke_auth`, and `/health` curl. Confirms `require_not_locked` wiring didn't break the golden path, 422-instead-of-401 doesn't confuse the auth smoke (it never hit a malformed-token path anyway), and the empty-`public/` fix restored the deploy.

**Deviations:**
1. `require_not_locked` applied to only one route (`PUT /auth/password`) instead of "every authenticated non-exempt route" as literally written. Driven by a spec ambiguity; resolved by tightening auth.md rather than applying to anonymous endpoints.
2. `verify-email` has a second 401 path (subject user missing after successful decode) left at 401 — flagged as an edge case; revisit if it surfaces in 0015 frontend error handling.
3. Frontend agent used the actual `ApiError(status, code, message)` positional constructor rather than the object-shape sketch in the ticket brief. Correct adaptation, not a regression.
4. Deleting `next.svg` broke Cloud Build. Root cause: agent brief did not include a "check Dockerfile COPY dependencies before deleting the last file in a directory" step. Postmortem flagged above.

**Commits:** 2a23b05 (audits + ticket open), 4fc4d33 (Phase 0+1), 888e0c9 (auth.md clarification), a8f1915 (public/.gitkeep hotfix).

**Deferred from audit (tracked):**
- Backend F-3 (`users.updated_at` best-effort accuracy) — informational only; revisit when the first filtering query on `updated_at` lands.
- Backend F-6 (`sleep 3` fragility in `cloudbuild.yaml` migrate step) — revisit on first intermittent migration failure.
- Frontend F-6 (`undefined as T` cast on 204 responses) — will be handled naturally when 0015 adds typed `api.get/post/delete` wrappers.
- Frontend F-9 (`.dockerignore *.md` nit) — no action needed unless a `README.md` in `frontend/` is ever required in the image.
- 0009 F-2 (viewport export on layout) and F-6 (tsconfig tightening) — still deferred from 0009 disposition.
- Frontend F-1/F-2/F-7 (401 refresh, auth context, middleware + route groups) — folded into 0015 where they have a real consumer.
