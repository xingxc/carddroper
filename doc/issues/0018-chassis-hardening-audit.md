---
id: 0018
title: chassis-hardening audit — find missing validators, grow chassis-contract.md
status: open (unblocked 2026-04-25)
priority: medium
found_by: 0015.5 landing the first chassis contract entry (CORS) + Option C coupling rule in CLAUDE.md
---

## Context

0015.5 established the pattern: `doc/operations/chassis-contract.md` lists every chassis invariant 1:1 with its enforcement (pydantic validator, middleware check, etc.). `CLAUDE.md` requires new validators to land with contract entries in the same commit.

That rule governs **new** validators added from here forward. But the chassis already has settings where an invariant is implicit but not enforced. A deliberate pass is needed to find them and either:
- Add the validator + contract entry, OR
- Note explicitly why the invariant doesn't warrant enforcement (documented exclusion).

This ticket is that deliberate pass.

## Candidate areas to audit (non-exhaustive — the audit may surface others)

Each candidate is a hypothesis; the audit confirms or rejects based on actual risk to adopters.

- **`JWT_SECRET`** — should have a minimum-length check (e.g. ≥32 chars) and refuse empty string. Current state: no validator. Adopter running chassis with a weak or missing secret would have working-but-insecure auth.
- **`SENDGRID_API_KEY`** — required when `SENDGRID_SANDBOX=false`. Currently the chassis just logs a fallback `dev_preview_url` if the key is missing, which is correct for dev but would silently fail to deliver production emails.
- **`DATABASE_URL`** — required in prod; `asyncpg` driver prefix expected. Currently defaults to a local postgres URL; no check that it's been overridden in prod.
- **`FROM_EMAIL` / `FROM_NAME`** — required when emails are wired. Today both have plausible defaults; prod misconfiguration (e.g. unresolvable `@example.com`) would cause SendGrid failures that look like infra issues.
- **`SENDGRID_TEMPLATE_*` IDs** — all required when sandbox is off. Missing any would crash at first send attempt rather than at boot.
- **`JWT_ISSUER` / `JWT_AUDIENCE`** — required; refuse empty string. Tokens minted without these would be rejected by the decoder, but the error happens at first auth attempt.
- **Cookie `secure` flag** — should be true in prod (non-localhost). Today's middleware likely handles this; confirm.
- **Rate-limit settings** (`RESEND_VERIFICATION_RATE_LIMIT` etc.) — sensible defaults exist; likely low-value to add validators, but flag any that must be non-zero.

- **Token-version-bump-and-cookie-clear pattern.** Endpoints that bump `user.token_version` on an authenticated session must clear the dead auth cookies (via `_clear_auth_cookies`) or re-issue fresh ones (via `_set_auth_cookies` with the new tv). Current audit: change-password (auth.py:381-388) re-issues fresh cookies ✓; reset-password (auth.py:448-) clears cookies on success (0016 fix for the edge case where reset is submitted from a device with an active session) ✓. Verify-email was formerly in this pattern but removed in 0015.8 — it no longer bumps `token_version` at all (capability toggle, not session reset). Future endpoints landing this pattern (e.g., change-email in ticket 0017, any admin "revoke session" feature) must honor the rule. Audit mechanic: `grep -n 'token_version += 1' backend/app/routes/` and classify each hit as "clears", "re-issues", "no session", or "intentionally none" with reasoning.

- **Pydantic-settings `extra="forbid"`.** Current `backend/app/config.py:16` sets `extra="ignore"`, so unknown env vars in `.env` or the process environment are silently dropped. A real typo was found in local `backend/.env` during 0016 Phase 2 setup: `FRONTEND_URL=...` silently ignored (correct field name is `FRONTEND_BASE_URL`), the app fell back to its default, no warning. Switch to `extra="forbid"` so `Settings()` construction raises loudly on unknown fields — same fail-loud posture as the CORS and cookie-domain validators. Before flipping: verify all env vars set by `cloudbuild.yaml` (staging + any future prod deploy) and by Cloud Run Secret Manager bindings correspond 1:1 with declared `Settings` fields (current staging appears clean — `SENDGRID_SANDBOX`, `FROM_EMAIL`, `FROM_NAME`, `FRONTEND_BASE_URL`, `CORS_ORIGINS`, `COOKIE_DOMAIN`, all the `SENDGRID_TEMPLATE_*` secrets, `DATABASE_URL`, `JWT_SECRET`, `SENDGRID_API_KEY` are all declared). If the flip causes a new invariant violation, pause and fix the env surface before landing. Contract consequence: adding "every env var the chassis reads must be a declared field on `Settings`" is a new invariant — gets a `chassis-contract.md` entry when 0018 lands this change.

- **Test-suite env-isolation discipline** — tests must never rely on `.env` defaults. Two patterns apply depending on how the tested code reads the setting:

  - **Runtime-path tests** (the tested code re-reads `settings.X` on each request — e.g., the register handler reading `settings.BILLING_ENABLED` inside its body): explicitly `patch.object(settings, "X", ...)` in the test scope. Effective because the runtime code re-reads settings per request.

  - **Feature-gated tests** (the tested code made its decision at module-import time — e.g., `main.py` mounting routes inside `if settings.BILLING_ENABLED`): use `pytestmark = pytest.mark.skipif(not settings.X, reason="...")` at module level if the whole file is gated, or `@pytest.mark.skipif(...)` per test for mixed files. Patching settings mid-test is useless here because routing is baked in.

  Scan `backend/tests/` for any tests violating either pattern. Origin: 0023.1 (`test_register_does_not_create_customer_when_billing_disabled` + `test_webhook_not_mounted_when_billing_disabled` were Kind-1; `test_billing_topup.py` entirely Kind-2). 0023.1 swept only the two billing test files; this audit applies the Kind-1/Kind-2 classification to the rest of `backend/tests/` (auth, email, password-reset, refresh-token, JWT, rate-limit, etc.). Audit deliverable: a classification table in the report (test_file → Kind-1 violations fixed → Kind-2 violations fixed → no violations), so future-you can grep for "did 0018 sweep test_X.py?" without re-running the audit.

- **GCP IAM least-privilege — default compute SA carries `Editor`.** Standard GCP project-creation behavior auto-creates a default Compute Engine service account (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`) and grants it the primitive `Editor` role at project level. Editor is one of three "primitive roles" (Owner / Editor / Viewer); it grants nearly all permissions except IAM admin — broadly considered overly permissive by GCP's own security baseline. Carddroper's real workloads run as dedicated SAs: Cloud Build uses `carddroper-build@carddroper-staging.iam.gserviceaccount.com`, Cloud Run runtime uses `carddroper-runtime@carddroper-staging.iam.gserviceaccount.com` (verified in `cloudbuild.yaml` deploy step). The default compute SA isn't actively used. But it remains latent attack surface: any service deployed without an explicit `--service-account` flag (a Cloud Run revision, a Cloud Function, a Cloud Scheduler job) auto-binds to the default compute SA and silently inherits Editor — exactly the misconfiguration class we want chassis defense-in-depth against. Two acceptable fixes: (a) downgrade the default compute SA's role to a minimal one (e.g., `roles/viewer` or no roles at all), or (b) delete the default compute SA entirely (GCP recreates it on demand if anything actually needs it, which would surface the dependency rather than masking it). Before either: confirm no live workload depends on the default SA — `gcloud run services list --format='value(metadata.name,spec.template.spec.serviceAccountName)' --project=carddroper-staging` and similar checks across Cloud Functions, Scheduler, etc. Apply the same audit to `carddroper-prod` when prod stands up (per PLAN.md §10.7). Origin: 2026-04-24 IAM review during 0023 staging-rollout setup; user observed `Editor (1) — 957070361052-compute@developer.gserviceaccount.com` in the IAM page.

- **`NEXT_PUBLIC_*` four-file invariant.** When the chassis adds a new build-time public env var (e.g., `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` from 0023), the value must be wired through four locations or it silently ships empty: (a) `frontend/Dockerfile` — `ARG NAME` + `ENV NAME=${NAME}` declarations; (b) `docker-compose.yml` — under `services.frontend.build.args`; (c) `cloudbuild.yaml` — frontend docker-build step `--build-arg NAME=$_VAR` substitution; (d) `frontend/.env.example` — so adopters know the var exists. 0023 cost real time: missing the `ARG` declaration in `frontend/Dockerfile` (commit 892bd66) caused `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` to silently bake as empty even after `--build-arg` was added to cloudbuild. Audit candidate: every `NEXT_PUBLIC_*` referenced in `frontend/lib/` or `frontend/components/` must appear in all four locations. Codify as a four-file checklist in `doc/operations/development.md` that adopters consult when adding new public env vars. Origin: 0023 rollout 2026-04-25.

- **Route-group URL gotcha.** Next.js App Router treats parens-wrapped folders (`(app)`, `(marketing)`, `(auth)`) as **route groups** — they organize files but **do not appear in the URL**. A page at `frontend/app/(app)/billing/page.tsx` serves `/billing`, NOT `/app/billing`. 0023 cost real time: the billing page was at `(app)/billing/page.tsx` and 404'd at `/app/billing` until moved to `(app)/app/billing/page.tsx` (commit 0946a49). Audit candidate: for any chassis route intended to live under a literal `/app/...` URL, verify the page file is at `(app)/app/<path>/page.tsx` (nested literal `app/`), not `(app)/<path>/page.tsx`. Add a one-paragraph note to `doc/architecture/site-model.md` (already documents the Canva-model auth wall) describing route-group URL behavior + the literal-`app/` convention. Origin: 0023 rollout 2026-04-25.

- **`printenv` is meaningless for `NEXT_PUBLIC_*` runtime debugging.** Next.js inlines `process.env.NEXT_PUBLIC_*` references at build time, replacing them with literal strings in the bundle output. The runtime container's OS env (visible via `printenv` in `docker-compose exec`) does NOT need the var set — what matters is that it was set at `npm run build` time. 0023 cost real time: `printenv` showed empty `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` in the running container, falsely suggesting the env was missing; the bundle had the correct value baked in (verified with `grep "pk_test_" /app/.next`). Audit candidate: codify the verification mechanic in `doc/operations/development.md` — for `NEXT_PUBLIC_*` debugging, grep the build output (`/app/.next` post-build) to confirm presence, not `printenv` against the running container. Origin: 0023 rollout 2026-04-25.

## Approach

The §Candidate areas list has 15 candidates. Steps below cover all of them; each step names which candidate(s) it addresses.

1. **Settings field audit.** Read `backend/app/config.py` end-to-end. For each field, classify: **must-be-valid-or-crash** (validator needed), **has-a-safe-default** (no action), **purely-product-tunable** (not in contract). Covers candidates: `JWT_SECRET`, `SENDGRID_API_KEY`, `DATABASE_URL`, `FROM_EMAIL`/`FROM_NAME`, `SENDGRID_TEMPLATE_*`, `JWT_ISSUER`/`JWT_AUDIENCE`, Cookie `secure`, rate-limit non-zero where applicable.

2. **Validator + chassis-contract coupling.** For each must-be-valid-or-crash field, write a pydantic validator on `Settings` + a `doc/operations/chassis-contract.md` entry in the **same commit** (per the `CLAUDE.md` coupling rule).

3. **Un-validated field rationale.** For any field audited and deliberately left un-validated (has-a-safe-default or purely-product-tunable), add a one-line comment in `config.py` explaining why — prevents future auditors from re-auditing the same ground.

4. **`extra="forbid"` flip with pre-flip env-var-surface check.** Before flipping: verify all env vars set by `cloudbuild.yaml` (and any future prod deploy) and Cloud Run Secret Manager bindings correspond 1:1 with declared `Settings` fields. If a mismatch exists, **PAUSE** and report — orchestrator dispatches the env-fix first. If clean, flip to `extra="forbid"` and add the chassis-contract entry: "every env var the chassis reads is a declared field on `Settings`." Covers candidate: Pydantic-settings `extra="forbid"`.

5. **Token-version-bump-and-cookie-clear codification.** No code change required (current endpoints already classified in the candidate body — change-password re-issues, reset-password clears, verify-email N/A per 0015.8). Add a `chassis-contract.md` entry stating the rule + the current endpoint classification table. Future endpoints (e.g., 0017 change-email) honor the rule via PR-time contract enforcement, not by re-opening 0018. Covers candidate: Token-version-bump-and-cookie-clear pattern.

6. **Test-suite Kind-1/Kind-2 sweep.** Apply the classification frame to every test file in `backend/tests/`. Fix violations: Kind-1 (runtime-path) → `patch.object(settings, ...)` in test scope; Kind-2 (feature-gated at module-import time) → `pytestmark = pytest.mark.skipif(...)` at module level. Produce a classification table for the report. Covers candidate: Test-suite env-isolation discipline.

7. **Two-state pytest verification.** Run `.venv/bin/pytest` under both `BILLING_ENABLED=true` and `BILLING_ENABLED=false` in `backend/.env`. Invariant: **zero failures** in either state. See `doc/operations/testing.md §Test isolation from env state`. Extend the pattern to any other chassis feature flag introduced by the audit.

8. **Three 0023-rollout doc candidates.** Land the doc additions described in their candidate bodies:
   - `doc/operations/development.md` — new section "NEXT_PUBLIC_* four-file checklist" (Dockerfile ARG+ENV, docker-compose args, cloudbuild --build-arg, .env.example).
   - `doc/operations/development.md` — new subsection "NEXT_PUBLIC_* runtime debugging" (grep `/app/.next` post-build; `printenv` is meaningless).
   - `doc/architecture/site-model.md` — paragraph on Next.js route-group URL behavior + the literal-`app/` convention.
   Covers candidates: NEXT_PUBLIC four-file invariant, route-group URL gotcha, `printenv` meaningless.

9. **GCP IAM (default compute SA) — user task.** Produce a `gcloud` command checklist in the report so the user can verify no live workload depends on the default SA, then downgrade or delete it. Do not attempt the change. AI agents never run scripts against real GCP per `doc/operations/testing.md`. Covers candidate: GCP IAM least-privilege.

10. **Commit grouping.** Batch the code/doc additions into 1–3 cohesive commits (e.g. "auth-side validators", "email-side validators", "extra=forbid + chassis-contract + doc additions"). Use judgment.

## Out of scope

- New chassis settings or features. The audit is pure hardening.
- Frontend settings audit. If the frontend has a chassis-contract equivalent later, that's its own audit.
- Running the audit against adopter projects. Adopters inherit the hardening for free once chassis is updated.
- **0017 (change-email)** — separate future ticket. Step 5 codifies the token-version rule that change-email's PR will honor at PR time; this ticket does **not** implement change-email.
- **Frontend code changes.** This ticket produces doc additions only (`development.md` + `site-model.md`). If the route-group URL audit finds a misnamed page under `frontend/app/`, **PAUSE** and report — orchestrator dispatches the rename to frontend-builder separately.
- **GCP IAM execution.** The default-compute-SA fix is a user task; this ticket produces a `gcloud` command checklist only. AI agents never run scripts against real GCP per `doc/operations/testing.md`.

## Scheduling

Originally framed as "wait until the auth surface is fully complete (0015, 0015.5, 0016, 0017) so the audit covers everything in one pass." As of 2026-04-25: 0015 ✓, 0015.5 ✓, 0016 ✓ (all resolved). 0017 (change-email) is not yet ticketed — only a design note in `PLAN.md §6 #8`.

Re-evaluation 2026-04-25 (after 0023 rollout): only the **token-version-bump-and-cookie-clear pattern** candidate has an auth-surface dependency, and its current-state classification (change-password ✓ re-issues; reset-password ✓ clears; verify-email N/A per 0015.8) is already substantively in the candidate's body. When 0017 (change-email) later lands, its PR adds one row to the audit by honoring the chassis-contract entry this audit codifies — not by re-opening 0018.

The remaining candidates (14 of 15) have no auth-surface dependency. Holding them back gains nothing; landing them now captures fresh material from the 0023 rollout (three adopter-snag gotchas + the test-isolation Kind-1/Kind-2 frame established by 0023.1) while context is hot. **Unblocked 2026-04-25.**

## Report

When executed:

- **Candidate classification table (headline deliverable).** For each of the 15 candidates: candidate name → action taken (validator-added / contract-entry-only / doc-added / deferred-to-user / no-action-with-rationale) → file(s) touched → commit hash. Every candidate must appear in this table.
- **Settings field audit table.** For each field in `backend/app/config.py`: name → classification (must-be-valid-or-crash / has-a-safe-default / purely-product-tunable / deliberately-un-validated) → action.
- Full diffs of `backend/app/config.py` (validators + un-validated comments) and `doc/operations/chassis-contract.md` (entries added).
- **Test-suite Kind-1/Kind-2 classification table.** For each file in `backend/tests/`: file → Kind-1 violations fixed (count) → Kind-2 violations fixed (count) → "no violations" if clean. List **every** file scanned.
- **Two-state pytest summary lines** — both `BILLING_ENABLED=true` and `BILLING_ENABLED=false` runs.
- `ruff check` + `ruff format --check` summary line.
- Full diffs of `doc/operations/development.md` and `doc/architecture/site-model.md` — new sections.
- **`gcloud` command checklist** for the user — covering verification of dependencies on the default compute SA + the downgrade/delete commands. User-runnable as-is.
- Any deviation from §Approach with reasoning.
- Anything **PAUSED** for orchestrator follow-up (env-var surface mismatch blocking `extra="forbid"`; misnamed frontend page; etc.).
- No staging deploy is performed by this dispatch. If a new invariant would cause staging to fail at startup, PAUSE before flipping `extra="forbid"` and report.

## Resolution

*(filled in on close)*
