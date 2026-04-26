---
id: 0017
title: change-email retroactive audit — verify spec compliance + close any gaps
status: open
priority: high (security-relevant: notification-to-old-address is the silent-account-takeover canary per PLAN.md §6 #8)
found_by: 0018 chassis-hardening audit (token-version-bump grep found `POST /auth/confirm-email-change` at `auth.py:682` without a corresponding ticket file)
---

## Context

`PLAN.md §6 #8` specifies the change-email flow:

> Email change flow. Standard money-handling pattern: re-prompt current password → verification link to the new address → flip `users.email` + bump `token_version` → notification to the **old** address ("your email was changed"). The notification to the old address is the canary that detects silent account takeover.

The 0018 audit's token-version-bump-and-cookie-clear classification surfaced that `POST /auth/confirm-email-change` already exists at `auth.py:682` — change-email landed in some prior session without a dedicated ticket file. The 0018 chassis-contract entry classifies the confirm endpoint correctly ("no session — reached via email link; session self-invalidates on next request via tv mismatch") but does not verify the **rest** of the flow exists or follows the spec.

This ticket is the retroactive audit: confirm what exists, identify gaps against the spec, close gaps inline within bounded scope, and codify the result.

### Why retroactive vs. ignore

Change-email is **security-relevant**. The notification to the old address is the canary that detects silent account takeover: if an attacker briefly compromises an account, changes the email, and the original owner never receives notification, the attacker gains permanent control. The chassis cannot afford a silent failure here. A retroactive audit is the chassis-reliability response — same posture as 0018 (audit before extending; surface silent failures before they bite).

### What 0018 already established

- `POST /auth/confirm-email-change` exists at `auth.py:682` and bumps `token_version`.
- `chassis-contract.md` token-version-bump classification table includes the confirm endpoint.
- `cloudbuild.yaml --set-secrets` declares both `SENDGRID_TEMPLATE_CHANGE_EMAIL` (verification to new address) and `SENDGRID_TEMPLATE_EMAIL_CHANGED` (notification to old address) — strong signal that both templates were intended.
- `0018 validate_sendgrid_production` requires both template IDs to be set when `SENDGRID_SANDBOX=false` + key non-empty.
- `site-model.md:32` mentions `change-email` in the `(auth)/` route group — strong signal that frontend exists.

These are signals, not verification. This ticket verifies.

## Audit checklist — items to classify (Phase 0a)

For each item below, classify as **present-and-correct**, **present-with-gap**, or **missing**. Produce a gap-analysis table in the report. Read paths in `backend/app/routes/auth.py`, `backend/app/services/email_service.py`, `backend/tests/`, and `frontend/app/(auth)/change-email/` (read-only for frontend).

### 1. Request endpoint — `PUT /auth/email` (or equivalent)

Expected behavior:

- Auth-required (`Depends(get_current_user)` or `require_verified` — verify which is correct; `require_verified` is the chassis convention for sensitive auth mutations).
- Body: `{current_password: str, new_email: str}` (Pydantic).
- Re-prompts current password and verifies via `bcrypt.checkpw` (the "money-handling pattern").
- Validates new email format (Pydantic `EmailStr`).
- If new email matches existing user → 409 (or generic to avoid enumeration — confirm the chassis convention; auth-related error codes per `0016.8`).
- Sends verification email to **new address** with signed token (`SENDGRID_TEMPLATE_CHANGE_EMAIL`).
- Does NOT yet flip `users.email` — that happens at confirm-time.
- Rate-limited (likely shares a rate limit with verify-resend or has its own).

### 2. Confirm endpoint — `POST /auth/confirm-email-change` at `auth.py:682`

Expected behavior:

- Public endpoint (no auth required — reached from the verification email link).
- Body: `{token: str}`.
- Verifies signed token (issued at request-time).
- Idempotency: same token consumed twice → 410 or 400.
- Looks up user by token's user_id claim.
- **Sends notification email to OLD address BEFORE flipping `users.email`** — see item 3.
- Flips `users.email` to the new address.
- Bumps `user.token_version` to invalidate any active sessions.
- Returns 200 with success message; does NOT set auth cookies (the user follows up by logging in with new email).
- Error states: expired token, already-used token, user-not-found, new-email-now-taken-by-another-user (race condition between request and confirm).

### 3. Notification email to OLD address — the security canary

This is the headline item. Verify:

- Sent at confirm-time, BEFORE the `users.email` flip.
- Uses `SENDGRID_TEMPLATE_EMAIL_CHANGED` template.
- Subject + body: "your email was changed" (or similar).
- Body includes both old AND new addresses so the user can identify the change.
- Body includes a recovery affordance — recommended: a password-reset link, OR explicit instructions ("if this wasn't you, reset your password immediately"). Confirm whatever was implemented.
- Sent BEFORE the email flip is critical: if sent after, a `users.email` flip + `send_email` failure cascade could leave the old owner with no notification AND no longer-controlled email. Pre-flip ordering ensures the canary fires even if the email flip itself succeeds and any later send fails.

If missing: this is the highest-severity gap. Land inline (see Approach §B).

### 4. Frontend request page — `(auth)/change-email/page.tsx`

Verify:

- Form: current password + new email.
- POSTs to the chassis API (path matches item 1).
- Handles success state ("verification link sent to your new address — check your inbox").
- Handles error states (wrong password, email taken, validation errors).
- Linked from somewhere in the authed UI (ProfileMenu Settings section, or a dedicated `/app/settings` page).

### 5. Frontend confirm page

The verification email link lands somewhere. Verify:

- Page exists at `(auth)/confirm-email-change/page.tsx` (or similar — the link target).
- Reads `token` from URL query string.
- POSTs to `POST /auth/confirm-email-change` (item 2).
- Handles success / expired / invalid token states.
- After success, redirects to `/login` with a "please log in with your new email" message.

### 6. Tests — `backend/tests/test_change_email.py` (or equivalent)

Verify presence of (at minimum):

- `test_change_email_requires_auth` — no token → 401.
- `test_change_email_requires_verified` — auth but unverified → 403 (if `require_verified` is the dep).
- `test_change_email_wrong_current_password` — wrong password → 401.
- `test_change_email_invalid_email_format` — `not-an-email` → 422.
- `test_change_email_already_taken` — new email matches existing user → appropriate response.
- `test_change_email_sends_verification_to_new_address` — assert `send_email` called with new address + `SENDGRID_TEMPLATE_CHANGE_EMAIL`.
- `test_change_email_does_not_flip_email_yet` — `users.email` unchanged after request.
- `test_confirm_email_change_invalid_token` — bad token → 400/410.
- `test_confirm_email_change_expired_token` — expired token → 400/410.
- `test_confirm_email_change_replays_token` — same token twice → second errors.
- **`test_confirm_email_change_sends_notification_to_old_address`** — the security canary. Assert `send_email` called with the OLD email address + `SENDGRID_TEMPLATE_EMAIL_CHANGED`. **This test is the safety net.**
- `test_confirm_email_change_flips_email` — `users.email` becomes new address.
- `test_confirm_email_change_bumps_token_version` — `users.token_version` increments.
- `test_confirm_email_change_notification_sent_before_flip` — if asserting ordering is feasible, verify the notification was sent BEFORE the email flip (e.g., by mocking `send_email` to fail and asserting the email is unchanged).

If any of these are missing — particularly the security-canary test — land them inline (see Approach §B).

### 7. Chassis-contract entries

The 0018 audit covered the SendGrid template requirement and the token-version-bump table. Likely no new contract entries needed. But verify:

- `SENDGRID_TEMPLATE_EMAIL_CHANGED` is in the validator's required-when-production list (it should be — `0018 validate_sendgrid_production` requires all five).
- The token-version-bump table in `chassis-contract.md` lists `confirm-email-change` correctly.
- If the audit surfaces a new **startup-time** invariant (e.g., a new `Settings` validator), add a `chassis-contract.md` entry per the coupling rule. Code-level disciplines like "notification sent before email flip" belong in code comments + tests, **not** in `chassis-contract.md` (the contract is for runtime-enforced invariants only — see `chassis-contract.md` preamble).

## Approach

The agent works in two halves. Phase 0a (audit) is non-destructive read-and-classify. Phase 0b (gap-fixing) is bounded inline implementation.

### Phase 0a — audit (read-only)

1. Read `backend/app/routes/auth.py` to find request/confirm endpoints. Identify the route paths used.
2. Read `backend/app/services/email_service.py` for the change-email + email-changed send paths.
3. Read `backend/tests/test_*.py` for any change-email-related tests; also `grep -rn "change.email\|change_email\|email_change\|email-change" backend/tests/`.
4. Read `frontend/app/(auth)/` for change-email + confirm pages (read-only — frontend changes are out of scope; PAUSE if frontend gaps found).
5. Produce the **gap-analysis table** for items 1–7 above.

### Phase 0b — close gaps within scope

Within scope for inline fixing (backend-builder dispatch):

- **Missing notification email to old address (item 3).** This is security-critical; land it inline with the matching `chassis-contract.md` entry if appropriate. Same commit as the test that proves it.
- **Missing tests for any item.** Land them inline. The security-canary test (item 6's `test_confirm_email_change_sends_notification_to_old_address`) is non-negotiable if the notification email is wired but untested.
- **Bug fixes in existing endpoints** (e.g., notification sent after flip instead of before, missing token_version bump, etc.). Land inline.
- **Missing chassis-contract entries** for invariants the audit codifies.

PAUSE conditions (do NOT work around):

- **Missing request endpoint** (item 1) — that's a substantial new endpoint with auth + rate-limit + validation; scope concern. PAUSE; orchestrator reviews and decides.
- **Missing confirm endpoint** (item 2) — same. (Less likely, since it exists at `auth.py:682`.)
- **Missing frontend pages** (items 4, 5) — out of backend-builder scope. PAUSE.
- **HIBP integration changes or password-policy changes** to the request endpoint — out of scope.
- **Race condition between request and confirm** (e.g., new email taken between request and confirm by another user) — if the existing impl handles this, document it; if not and a fix is non-trivial, PAUSE.

## Out of scope

- Account-deletion flow.
- Email-change frequency rate-limit changes (existing limits, if any, stay as-is).
- Old-email "this wasn't me" recovery beyond what's already wired (if the notification email currently has no recovery link, recommend adding one but do NOT scope a UI change here — flag for follow-up).
- Frontend code changes — read-only audit only; PAUSE if frontend gaps exist.
- HIBP check on new email — the HIBP discipline applies to passwords, not emails (no password leak DB applies). Skip.

## Acceptance

1. Phase 0a gap-analysis table produced for items 1–7.
2. All in-scope gaps closed inline; full diffs of changed files in the report.
3. New tests added for any items missing tests; **security-canary test mandatory if notification email is wired**.
4. Two-state pytest green: `BILLING_ENABLED=true` and `BILLING_ENABLED=false` — zero failures in either state.
5. `ruff check .` + `ruff format --check .` clean.
6. `chassis-contract.md` updated if a new invariant is codified (per coupling rule).
7. Anything PAUSED is reported with proposed scoping for follow-up.

## Verification

**Automated:**

- `.venv/bin/pytest` summary lines for both `BILLING_ENABLED` states.
- `ruff check` / `ruff format --check` summary.
- Paste the change-email-related tests added (full bodies for the security-canary test).

**Functional:**

- The audit output itself is the deliverable — the gap-analysis table tells the orchestrator whether a follow-up is needed.

## Report

- **Gap-analysis table** for items 1–7: item → classification (present-and-correct / present-with-gap / missing) → action (none / inline-fix / PAUSE).
- For each inline fix: file(s) touched, before/after summary, test added.
- Full diffs of any modified `routes/auth.py`, `services/email_service.py`, test files, `chassis-contract.md`.
- Two-state pytest summary lines.
- ruff summary.
- Anything PAUSED with proposed follow-up ticket scope.
- Confirmation that the security-canary test (notification sent to old address) exists and passes.

## Resolution

*(filled in by orchestrator after agent reports)*
