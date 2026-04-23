---
id: 0016
title: forgot-password + reset-password pages + /login "Forgot password?" link + reset ghost-session fix
status: resolved
priority: high
found_by: 0015 Phase 1 deferrals (forgot-password link + pages); 0016 pre-draft audit (reset-password ghost-session class matches pre-0015.7 verify-email bug)
---

## Context

Backend endpoints exist and behave correctly:
- `POST /auth/forgot-password` (`backend/app/routes/auth.py:397-421`): rate-limited `3/hour` per IP (`FORGOT_PASSWORD_RATE_LIMIT`), always returns `200` with a neutral message regardless of whether the email is registered. Anti-enumeration by design.
- `GET /auth/validate-reset-token?token=...` (`auth.py:429-441`): validates token signature + `purpose` + `tv` match. Always `200` with `{"valid": bool, "reason": str?}` — no 4xx branches. Non-consuming preview endpoint explicitly meant for the frontend to show "invalid / expired link" without typing a password first (auth.md §Password reset line 66).
- `POST /auth/reset-password` (`auth.py:448-468`): validates token, enforces password policy (length + HIBP breach check via `_enforce_password_policy`), updates `password_hash`, bumps `token_version`, revokes refresh tokens. Currently returns a bare dict on success — **no Set-Cookie clearing headers**.

Frontend chassis is in place from 0015: `(auth)/` route group, `FormField` / `FormError` / `SubmitButton` helpers, typed `api.get/post` wrappers, 401 silent-refresh interceptor already refresh-exempts `/auth/forgot-password` and `/auth/reset-password`. SendGrid `RESET_PASSWORD` template is wired in `cloudbuild.yaml` staging deploy. Email deliverability separately tracked in `0019` (doesn't block 0016).

Three gaps to close, one of which is a bug discovered during pre-draft audit:

1. **Forgot-password page** (`/forgot-password`) — doesn't exist.
2. **Reset-password page** (`/reset-password?token=`) — doesn't exist.
3. **"Forgot password?" link on `/login`** — explicitly deferred in 0015 Phase 1 (ticket 0015 line 112).
4. **Reset-password ghost-session bug** (audit finding, backend fix): `/auth/reset-password` success bumps `token_version` and revokes refresh tokens but does not clear the browser's `access_token` / `refresh_token` cookies. If the user happens to be logged in on the device that clicks the reset link (edge case: forgot password on phone but still logged in on laptop, uses reset link from laptop), post-reset redirect to `/login` triggers the proxy's cookie-presence check → 307 to `/app` → `/auth/me` 401 → user lands on `/app` with `user=undefined`. Same class of bug 0015.7 originally addressed for verify-email. Unlike verify-email (where 0015.8 removed the `token_version` bump entirely because verify is a capability toggle, not a security event), **reset-password's `token_version` bump is correct** — reset IS a genuine security event, sessions must die. So the fix shape mirrors `logout`: return `JSONResponse` with `_clear_auth_cookies(response)` on success.

## Design decisions (pre-committed)

- **Validate-first on mount for `/reset-password`.** `GET /auth/validate-reset-token` is called once in a `useEffect` guarded by a `useRef` (React 19 strict-mode double-mount protection, same pattern as `VerifyEmailBody`). Render states: pending / valid (password form) / invalid (error panel with CTA to `/forgot-password`). Only the submit path POSTs to `/auth/reset-password`.
- **Forgot-password neutral success.** After submit — regardless of backend response — render a neutral success panel: *"If an account exists with that email, we've sent a reset link. Check your inbox."* This mirrors the backend's anti-enumeration behavior. Don't reveal whether the email was known.
- **Password policy in forms.** Zod enforces `min(10)` matching `/register`. Password + confirmPassword match-refine, same shape as register. HIBP's breach-check rejection surfaces as a backend `422` with a specific message — render via `setError("newPassword", { message: err.message })`, following the precedent confirmed correct in the register page audit.
- **No toast system.** Inline feedback only, per all of 0015.
- **Middleware unchanged.** `/forgot-password` and `/reset-password` render for both authed and unauth'd users — no proxy redirect. Existing `/login` and `/register` authed-redirect-to-`/app` rules stay. Rationale: a logged-in user legitimately might want to reset their password; blocking them with a redirect would be surprising.
- **Forgot-password page on `/login`.** Single link below the password field (above submit), linking to `/forgot-password`. Label: "Forgot password?" — matches industry convention.
- **Post-reset redirect.** On successful reset, `router.push('/login?reset=success')`. The login page renders a dismissable banner *"Password reset successful. Please sign in with your new password."* when the query param is present. No form pre-fill.
- **Backend reset-cookie-clear sits in the 0018 audit pattern.** Moves reset-password from "pre-auth ✓" classification to "clears ✓". Update the 0018 bullet in the same commit.
- **No change to `_clear_auth_cookies` helper, no change to other endpoints.** Minimal surface area.
- **Not a chassis-contract invariant.** This is a behavioral rule (endpoints that bump `token_version` clear cookies on responses), tracked in 0018's audit pattern, not a startup check.

## Acceptance

### Phase 0 — backend fix (backend-builder, minimal)

1. **`backend/app/routes/auth.py::reset_password`** — change the success path to return `JSONResponse` with cookies cleared. Exact before/after:

   Before (auth.py:448-468):
   ```python
   @router.post("/reset-password", response_model=MessageResponse)
   async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
       payload = decode_reset_token(body.token)
       if not payload:
           raise validation_error("Invalid or expired reset token.")

       result = await db.execute(select(User).where(User.id == int(payload["sub"])))
       user = result.scalar_one_or_none()
       if not user or user.token_version != payload["tv"]:
           raise unauthorized("This reset link has already been used.")

       await _enforce_password_policy(body.new_password)

       user.password_hash = hash_password(body.new_password)
       user.token_version += 1
       await revoke_all_user_tokens(user.id, db)

       return {"message": "Password reset successfully. Please log in."}
   ```

   After:
   ```python
   @router.post("/reset-password", response_model=MessageResponse)
   async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
       payload = decode_reset_token(body.token)
       if not payload:
           raise validation_error("Invalid or expired reset token.")

       result = await db.execute(select(User).where(User.id == int(payload["sub"])))
       user = result.scalar_one_or_none()
       if not user or user.token_version != payload["tv"]:
           raise unauthorized("This reset link has already been used.")

       await _enforce_password_policy(body.new_password)

       user.password_hash = hash_password(body.new_password)
       user.token_version += 1
       await revoke_all_user_tokens(user.id, db)

       # Server-side invalidation happened above. If the caller happened to have
       # an active session (edge case: reset from a device where the user was
       # still logged in), clear the dead cookies so the proxy doesn't redirect
       # /login → /app based on stale cookie presence. Mirrors the logout
       # pattern; contrast with verify-email (0015.8) which removed its
       # token_version bump entirely.
       response = JSONResponse(content={"message": "Password reset successfully. Please log in."})
       _clear_auth_cookies(response)
       return response
   ```

   No new imports (`JSONResponse` + `_clear_auth_cookies` both already in scope).

2. **`backend/tests/test_auth_flow.py`** — add one test: `test_reset_password_clears_cookies`:
   - Register a user. Mint a reset token via `create_reset_token(user.id, user.token_version)`.
   - POST `/auth/reset-password` with `{token, new_password}` using a fresh password satisfying the policy (e.g. `"FreshPasswordTest1234"` — unlikely to appear in HIBP; if the test's HIBP check is stubbed/mocked, follow the existing suite's convention).
   - Assert 200 and body `{"message": "Password reset successfully. Please log in."}`.
   - Assert the response `Set-Cookie` headers include `Max-Age=0` clearing headers for both `access_token` and `refresh_token` (same assertion style as the 0015.7 cookie-clear tests before they were deleted in 0015.8).
   - Assert `user.token_version` went from N → N+1 and `user.password_hash` changed (defense — catches regressions where the endpoint short-circuits).

   If there's an existing `test_password_reset_flow` or similar, leave it intact — add the new test alongside.

3. **`doc/issues/0018-chassis-hardening-audit.md`** — update the token-version-bump-and-cookie-clear audit bullet. Current text says `reset-password (auth.py:462-463) is pre-auth ✓`. Change to:
   > `reset-password (auth.py:448-) clears cookies on success (0016 fix for the edge case where reset is submitted from a device with an active session) ✓`

### Phase 1 — frontend pages + login link (frontend-builder)

4. **`frontend/app/(auth)/forgot-password/page.tsx`** — new page.
   - Zod schema: `{ email: z.string().email(...) }`.
   - `useState<"idle" | "submitted">` tracks whether we've shown the neutral success panel.
   - `idle` state: email input (RHF + `FormField`), `<SubmitButton>`, FormError for network failures.
   - Submit: `api.post('/auth/forgot-password', { email: values.email })`. On success OR on any `ApiError` other than 429/network → set state to `submitted` (backend is anti-enumeration; we mirror that client-side). On 429 → form-level error "Too many attempts, please try again later." On NETWORK_ERROR → form-level retry prompt.
   - `submitted` state: render neutral panel: *"If an account exists with that email, we've sent a password reset link. Check your inbox."* Plus a small text link *"← Back to sign in"* to `/login`.
   - No "resend" button — rate limit is harsh (3/hour) and the 15m token TTL means reattempts usually go through the forgot-password form again.

5. **`frontend/app/(auth)/reset-password/page.tsx`** + **`frontend/app/(auth)/reset-password/ResetPasswordBody.tsx`** — mirrors the `verify-email/page.tsx` + `VerifyEmailBody.tsx` split (Suspense shell + client body, required because `useSearchParams` suspends).
   - Shell: `<Suspense>` wrapping `<ResetPasswordBody />`.
   - Body:
     - `useSearchParams().get("token")`. If missing → render invalid-link panel immediately, no mutation.
     - On mount (React 19 `useRef` guard), call `GET /auth/validate-reset-token?token=...`. States:
       - **pending:** centered spinner + "Checking your reset link…"
       - **validation failed (`{valid: false}`) OR missing token:** invalid-link panel — "This reset link is invalid or expired." + primary button to `/forgot-password` ("Request a new link") + secondary link to `/login`.
       - **validation OK (`{valid: true}`):** render the password reset form (below).
     - **Password form** (shown only on validation OK):
       - Zod: `newPassword` min 10, `confirmPassword` match-refine.
       - `FormField` × 2, `FormError`, `SubmitButton`.
       - Submit: `api.post('/auth/reset-password', { token, new_password: values.newPassword })`.
       - On success: `router.push('/login?reset=success')`. Do NOT call `markLoggedIn()` — user has no session after reset (cookies were cleared by the backend), they must log in fresh.
       - Error mapping: 422 → field-level on newPassword via `setError` with `err.message` (catches HIBP breach check). 401 → form-level error "This reset link has already been used." (and a link to `/forgot-password`). NETWORK_ERROR → retry prompt. Other → form-level `err.message`.

6. **`frontend/app/(auth)/login/page.tsx`** — add a "Forgot password?" link.
   - Placement: below the password field, above the submit button. Small text, right-aligned, linking to `/forgot-password`.
   - Use the same Tailwind link class as the existing "Register" link in the page header (`text-blue-600 hover:underline text-sm`).
   - Also: add a success-banner render when `useSearchParams().get("reset") === "success"`. Banner text: *"Password reset successful. Please sign in with your new password."* Dismissable (`useState<boolean>` toggle) or just renders above the form and disappears on first keystroke. Simplest: dismissable banner with an × button. If `useSearchParams` suspends, wrap the login body in Suspense like verify-email (check whether this is needed; if not, skip the split).

7. **No new helpers.** `FormField`, `FormError`, `SubmitButton` cover everything.

### Phase 2 — staging smoke (user)

8. Push main → Cloud Build redeploy.
9. Run smoke battery against staging — no new smoke script; existing four still apply. Reset-password is not in `smoke_auth.py` coverage (reset tokens can't be minted over public API, same limitation as verify-email). Add it to `smoke_verify_email.py`-style manual-only coverage if a need arises later.
10. Manual browser walkthrough:
    a. From `/login`, click "Forgot password?" → land on `/forgot-password`.
    b. Submit with your real email → see neutral success panel.
    c. Open inbox → click the reset link → land on `/reset-password?token=...` → "Checking your reset link…" → form renders.
    d. Enter matching new passwords (≥10 chars, non-breached) → submit → land on `/login` with "Password reset successful" banner.
    e. Log in with the new password → land on `/app`, authenticated, verified.
    f. **Edge-case check:** while logged in on `/app`, manually paste a valid reset link in the address bar (requires obtaining the link from a fresh forgot-password flow). Submit a new password → land on `/login?reset=success`. Confirm the browser's cookies are actually cleared (DevTools → Application → Cookies — `access_token` and `refresh_token` should be absent or have `expires` in the past). Confirm visiting `/app` now redirects to `/login` (proxy sees no cookies).
    g. **Invalid-link check:** visit `/reset-password?token=not-a-real-token` → see the invalid-link panel.
    h. **Stale-token check:** obtain a reset link, reset successfully, then click the same link a second time → see invalid-link panel ("This reset link has already been used.") — this is the 401 from `validate-reset-token` (`{valid: false}` with reason).

## Verification

**Automated (backend-builder Phase 0 report):**
- `pytest` green, including new `test_reset_password_clears_cookies`.
- `ruff` clean.
- Paste one captured Set-Cookie header from the new test (the `access_token` clearing line) verbatim.
- Paste before/after of the `reset_password` endpoint success branch.
- Paste before/after of the 0018 audit bullet.

**Automated (frontend-builder Phase 1 report):**
- `npm run lint`, `npx tsc --noEmit`, `npm run build` clean.
- Files created/modified list + one-line what-changed each.
- Paste the Zod schema of the reset form.
- Paste the error-mapping block for `/reset-password` submission.
- Paste the one-line JSX addition to `/login` for the forgot-password link + the success-banner block.
- Confirm no unused imports.

**Functional (user, Phase 2):**
- Walkthrough steps a–h all pass.
- Regression: 0015 golden path (register → verify → login → /app) still green — forgot/reset introduces no auth-flow regressions.

## Out of scope

- Changing the `/auth/forgot-password` rate limit or anti-enumeration posture.
- Adding a rate limit to `/auth/reset-password` (token-based single-use is the throttle; see Finding 5 of the pre-draft audit).
- Frontend testing infrastructure (Jest / Vitest / Playwright). Manual verification continues.
- Updating `smoke_auth.py` to exercise reset — reset tokens can't be minted over public API.
- Any change to `/auth/verify-email`, `/auth/register`, `/auth/login`, or `/auth/me`. Unchanged.
- HIBP changes (helper already in place and proven via register path audit).
- Any chassis-contract.md entry. This is a behavioral rule, tracked in 0018.
- Mobile-app flows for reset-password (no mobile client ships in v0.1.0).

## Chassis note

The shape — `(auth)/forgot-password/page.tsx`, `(auth)/reset-password/page.tsx` (split with Suspense body), `/login` forgot-link, and the backend cookie-clearing on reset — is **chassis**. Every future project built on this chassis will have a forgot/reset flow with identical mechanics. The *copy* (brand name, CTA wording) is body. When the chassis is extracted later, these pages and the login-link pattern are included in the starter.

## Report

Backend-builder (Phase 0):
- Files touched + one-line what-changed each.
- Before/after of `reset_password` endpoint (paste both).
- New test name + one-sentence summary.
- `pytest` summary line.
- `ruff` + `ruff format --check` summary lines.
- Before/after of the 0018 audit bullet.
- Captured `Set-Cookie` clearing header from the new test (one verbatim).
- Any deviation.

Frontend-builder (Phase 1):
- Files created/modified + one-line what-changed each.
- Paste the Zod schemas for forgot-password and reset-password forms.
- Paste the reset-password error-mapping block.
- Paste the `/login` forgot-link JSX addition AND the success-banner block.
- `npm run lint` / `npx tsc --noEmit` / `npm run build` summary lines.
- Confirm no unused imports.
- Any deviation.

Orchestrator (on close):
- Cloud Build SUCCESS link.
- Walkthrough step results for a–h.
- Any regression on prior 0015 flows.

## Resolution

Landed on main (2026-04-22, commit `75e55ae`). Forgot + reset pages shipped with the reset-password ghost-session fix — endpoint returns `JSONResponse` that clears auth cookies on success. Phase 2 staging cross-device walkthrough surfaced two follow-ups: (a) Cloud Run GFE rate-limit IP resolution → 0016.1 (`b12e1d0`); (b) victim-device stale-cookie ghost state after cross-device reset → 0016.2 + 0016.3 (`6d9ebbe`, `884a060`). The session-lifecycle work continued through 0016.4–0016.8 — see individual tickets.
