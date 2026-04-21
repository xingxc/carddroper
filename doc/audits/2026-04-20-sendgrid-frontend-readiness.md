# SendGrid Frontend Readiness Audit — 2026-04-20

**Auditor:** frontend-builder agent
**Ticket:** 0010 (SendGrid infrastructure)
**Scope:** Read-only static analysis of `/Users/johnxing/mini/postapp/frontend/**` plus reference reads of `doc/systems/auth.md`, `doc/reference/backend-api.md`, and `backend/app/routes/auth.py` to verify response shapes.

---

## 1. Email-Adjacent Flows Inventory

### Context: What the Carddroper frontend currently contains

The frontend scaffold (as of 2026-04-20) contains exactly five files with meaningful logic:

- `app/layout.tsx` — root layout, `<Providers>` wrapper, metadata title "Carddroper"
- `app/page.tsx` — single `<h1>Carddroper</h1>`
- `app/providers.tsx` — `QueryClient` with `staleTime: 30_000`, no auth context
- `lib/api.ts` — `apiFetch<T>`, `ApiError`, `ApiErrorBody`
- Various config/tooling files (`next.config.ts`, `tsconfig.json`, `eslint.config.mjs`, etc.)

**There are zero auth pages, zero email-related pages, and zero components** in the Carddroper frontend. The flows documented below are those that MUST exist per `doc/systems/auth.md` and that are present in the foodapp reference codebase — they have not yet been built for Carddroper.

The foodapp reference (`/Users/johnxing/mini/foodapp/frontend/src/`) is the pattern source. Its relevant auth pages were audited as the intended shape for Carddroper's upcoming ticket 0011.

---

### Flow A: Sign-up → verification email

**Page / component / handler:** NOT BUILT. Per `doc/systems/auth.md`, must become `app/(auth)/register/page.tsx`. Foodapp reference: `src/app/register/page.tsx` (a `RegisterForm` component inside `<Elements stripe={stripePromise}>`).

**Backend endpoint:** `POST /auth/register`

**Request body (from `backend/app/routes/auth.py:RegisterRequest`):**
```json
{ "email": "...", "password": "...", "full_name": "..." }
```
Note: `full_name` is `Optional[str]`. The foodapp sends additional fields (`billing_name`, `stripe_payment_method_id`) that the Carddroper backend does NOT accept — foodapp-specific fields would be silently ignored or cause a 422 if Pydantic is strict.

**Response shape the backend actually returns (`AuthResponse` at line 150):**
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque>",
  "user": {
    "id": 1,
    "email": "...",
    "full_name": "...",
    "verified_at": null
  }
}
```
Cookies `access_token` and `refresh_token` are also set HttpOnly. The response body also contains the raw tokens (dual delivery — web uses cookies, mobile would use body).

**Status codes:** 200 on success. 409 if email exists (`"A user with this email already exists."`). 422 on validation failure (Pydantic). 429 on rate limit (3/min). 400 on weak/breached password (HIBP check).

**UI state on success (foodapp pattern, not yet built for Carddroper):** Foodapp redirects to `/catalog`. Per `doc/systems/auth.md`, Carddroper's intended UX is to redirect to `/verify-email-sent` (a page that does not exist in either frontend today), which explains what's happening and provides a "resend" button.

**UI state on error (foodapp pattern):** `setError(getErrorMessage(err, "Registration failed. Please try again."))` — a generic fallback. No explicit handling of `CONFLICT` (409) or `RATE_LIMITED` (429) error codes. The foodapp swallows all errors into the same red banner with a generic message.

**Copy naming the brand or email:** The foodapp register page contains NO copy referencing "Carddroper", "noreply@carddroper.com", or "check your inbox." The success state after register (redirect to catalog) shows NO email-sent confirmation at all. The intended Carddroper UX (`/verify-email-sent`) does not yet exist.

**Email sent by backend:** `send_verification_email(user.email, verify_token, user.full_name)` — best-effort, never fails the request. After 0010 lands this becomes `send_email(template=EmailTemplate.VERIFY_EMAIL, to=..., dynamic_template_data=...)` with the same best-effort semantics.

---

### Flow B: Forgot-password → reset email

**Page / component / handler:** NOT BUILT for Carddroper. Foodapp reference: `src/app/forgot-password/page.tsx` (file:line foodapp reference `ForgotPasswordPage` at line 9).

**Backend endpoint:** `POST /auth/forgot-password`

**Request body (`ForgotPasswordRequest` at `auth.py:165`):**
```json
{ "email": "user@example.com" }
```

**Response shape backend returns:**
```json
{ "message": "If an account exists with that email, a reset link has been sent." }
```
Status 200 always (enumeration-safe). Status 429 on rate limit (3/hour).

**UI state on success (foodapp pattern):** `setSubmitted(true)` — renders a green banner: `"If an account exists with that email, a reset link has been sent. Check your inbox (and spam folder)."` with a "Back to login" link. No timer, no abort, no resend CTA from this screen.

**UI state on error (foodapp pattern, `forgot-password/page.tsx:34`):** Generic catch-all: `setError("Something went wrong. Please try again.")` — does NOT propagate the backend error code or message at all. A rate-limit 429 response would show the same generic error as a 500.

**Copy naming the brand or email:** Foodapp copy says `"Check your inbox (and spam folder)."` — no Carddroper brand name, no sender address mentioned in UI.

**Email sent by backend:** `send_password_reset(user.email, token, user.full_name)` — best-effort. After 0010: `send_email(template=EmailTemplate.RESET_PASSWORD, ...)` with same semantics.

---

### Flow C: Change-email → confirmation emails

**Page / component / handler:** NOT BUILT for Carddroper. The foodapp `src/app/profile/page.tsx` does NOT contain a change-email section either — it has sections for Personal Info (name/phone only), Password, Restaurant, and Billing. There is no `POST /auth/change-email` call anywhere in the foodapp frontend. There is no `POST /auth/confirm-email-change` call anywhere in the foodapp frontend.

**Backend endpoint:** `POST /auth/change-email` and `POST /auth/confirm-email-change`

**Request body for `change-email` (`ChangeEmailRequest` at `auth.py:178`):**
```json
{ "current_password": "...", "new_email": "user@newdomain.com" }
```

**Request body for `confirm-email-change` (`ConfirmEmailChangeRequest` at `auth.py:183`):**
```json
{ "token": "<jwt>" }
```

**Response shapes:**
- `POST /auth/change-email`: `{ "message": "Verification link sent to the new address." }` (200). Errors: 401 wrong password, 400 same email, 409 email in use, 429 rate limit.
- `POST /auth/confirm-email-change`: `{ "message": "Email changed. Please log in with your new email." }` (200). Errors: 401 invalid/expired token, 409 email race condition, 429 rate limit.

**UI state on success:** NO UI EXISTS for either endpoint in Carddroper or in the foodapp pattern.

**Emails sent by backend:**
- `change-email` → `send_email_change_verification(body.new_email, token, current_user.full_name)` to the new address.
- `confirm-email-change` → `send_email_change_notification(old_email, new_email)` to the old address.

Per `doc/systems/auth.md`: the notification email to the old address is the security canary — *"Your email on carddroper was changed to `<new_email>` on `<date>`. If this wasn't you, contact `support@carddroper.com` immediately."* This copy is entirely backend/SendGrid template territory; no frontend string.

---

### Flow D: Resend-verification

**Page / component / handler:** NOT BUILT for Carddroper. No `/verify-email-sent` page exists. No "resend" button exists anywhere. Foodapp has no equivalent page either.

**Backend endpoint:** `POST /auth/resend-verification`

**Auth required:** YES — requires a valid access token (cookie). This is the only email-trigger endpoint that requires authentication.

**Request body:** None (no body). The endpoint reads `current_user` from the access token cookie.

**Response shape:**
- If already verified: `{ "message": "Email already verified." }` (200)
- Otherwise: `{ "message": "Verification email sent." }` (200)
- 401 if not authenticated, 429 if rate limited (3/hour per `doc/systems/auth.md`).

**UI state on success:** NO UI EXISTS.

**Email sent by backend:** `send_verification_email(current_user.email, token, current_user.full_name)` — best-effort. After 0010: `send_email(template=EmailTemplate.VERIFY_EMAIL, ...)`.

---

### Flow E: Email-verification landing page (consumer of the verify link)

**Page / component / handler:** NOT BUILT for Carddroper. Foodapp has no equivalent page. Per `doc/systems/auth.md`, this must be `app/(auth)/verify-email/page.tsx`, receiving `?token=<jwt>` as a query param.

**Backend endpoint:** `POST /auth/verify-email`

**Request body (`VerifyEmailRequest` at `auth.py:174`):**
```json
{ "token": "<jwt>" }
```
The token comes from the query param in the email link; the page must extract it from the URL and POST it to the backend.

**Response shapes:**
- Success: `{ "message": "Email verified." }` (200)
- Already verified: `{ "message": "Email already verified." }` (200) — same status code, different message
- Invalid/expired token: 401 `{ "error": { "code": "UNAUTHORIZED", "message": "Invalid or expired verification token." } }`
- No token in URL: the page must handle this client-side (no backend call needed)
- 429 on rate limit (10/min)

**UI state on success:** NOT DESIGNED. The `doc/systems/auth.md` does not specify the post-verification redirect. The foodapp has no parallel flow. A logical UX would be to redirect to `/login` with a success banner, or auto-redirect to `/dashboard` if a session cookie exists.

**Note on verify-email endpoint method:** The `doc/reference/backend-api.md` table says `POST /auth/verify-email`. The actual backend route (`auth.py:452`) confirms `@router.post("/verify-email")`. However, `doc/systems/auth.md` at §Email verification step 2 says "The frontend page calls `POST /auth/verify-email` with the token" — confirmed consistent.

---

### Flow F: Confirm-email-change landing page (consumer of the email-change link)

**Page / component / handler:** NOT BUILT for Carddroper. Foodapp has no equivalent. There is no UI for `POST /auth/confirm-email-change`. The link sent to the new address (`https://carddroper.com/confirm-email-change?token=<jwt>` — URL path inferred, not specified in `doc/systems/auth.md`) has no frontend consumer.

---

## 2. Findings

### F-1: `/verify-email-sent` page does not exist — critical UX gap for 0010's immediate successor

**Severity:** blocker (for ticket 0011; not for 0010 itself)
**Frontend assumption:** After `POST /auth/register` succeeds, the user needs to be directed to a page explaining that a verification email was sent, with a "resend" button. `doc/systems/auth.md` explicitly names this page.
**Reality after 0010:** The backend will send a `VERIFY_EMAIL` SendGrid Dynamic Template email on register. The frontend has nowhere to send the user after registration that explains this.
**Recommendation:** Ticket 0011 must create `app/(auth)/verify-email-sent/page.tsx` before the registration page is built. The register success handler should redirect there, not to `/dashboard`.

---

### F-2: No `POST /auth/verify-email` landing page — verification links from 0010's email are dead links

**Severity:** blocker (for ticket 0011; not for 0010 itself)
**Frontend assumption:** None (page does not exist).
**Reality after 0010:** SendGrid will deliver emails containing a link like `https://carddroper.com/verify-email?token=<jwt>`. The frontend has no route at `/verify-email`. Clicking the link returns a Next.js 404.
**Recommendation:** Ticket 0011 must create `app/(auth)/verify-email/page.tsx` that extracts the `?token` query param and calls `POST /auth/verify-email`.

---

### F-3: No `POST /auth/confirm-email-change` landing page — email-change confirmation links are dead links

**Severity:** high
**Frontend assumption:** None (page does not exist).
**Reality after 0010:** `confirm-email-change` emails will contain a link (URL path unspecified in any doc). No frontend route exists to consume it.
**Recommendation:** Define the URL path in `doc/systems/auth.md` and create the page in a future ticket. Ticket 0011 callout: add a note about this gap.

---

### F-4: No change-email UI at all — the backend endpoint is orphaned

**Severity:** high
**Frontend assumption:** A profile/account-settings page will expose the change-email flow.
**Reality after 0010:** The backend has `POST /auth/change-email` fully implemented. Neither the Carddroper frontend nor the foodapp pattern has any UI for it. There is no "change email" section in the foodapp profile page.
**Recommendation:** Scope the change-email UI into whichever ticket builds the account/settings page. Do not scope into 0011 (that ticket already covers registration + verification).

---

### F-5: No resend-verification UI — users who miss the email are stuck

**Severity:** high
**Frontend assumption:** A `/verify-email-sent` page exists with a resend button.
**Reality after 0010:** The backend `POST /auth/resend-verification` endpoint is implemented and working. No frontend calls it. A user who does not receive the verification email (spam folder, typo, etc.) has no self-service path.
**Recommendation:** The `/verify-email-sent` page (F-1) must include a resend button that calls `POST /auth/resend-verification`. Rate limit: 3/hour per auth.md — the UI should grey out the button after one use with a timer or disable-until-reload pattern.

---

### F-6: Forgot-password error handling is silent on rate limiting — misleading UX

**Severity:** medium
**Frontend assumption (foodapp pattern, `forgot-password/page.tsx:34`):** All errors produce `"Something went wrong. Please try again."` — a generic message regardless of status code.
**Reality after 0010:** `POST /auth/forgot-password` is rate-limited at 3/hour. If a user hits this limit (e.g., spam-clicking submit), the backend returns 429 with `{ "error": { "code": "RATE_LIMITED", ... } }`. The UI shows the generic message, not a useful "You've requested too many resets. Try again in an hour." The user then tries again, hitting the limit again.
**Recommendation:** When building the forgot-password page for Carddroper, catch `ApiError` with `status === 429` or `code === "RATE_LIMITED"` and show a specific message.

---

### F-7: Register error handling does not distinguish conflict from validation — misleading UX

**Severity:** medium
**Frontend assumption (foodapp pattern, `register/page.tsx:129`):** `getErrorMessage(err, "Registration failed. Please try again.")` — wraps all errors generically except those with a backend message.
**Reality:** `POST /auth/register` returns 409 on duplicate email and 400 on weak password (HIBP breach check message). The foodapp's `getErrorMessage` does forward the backend `message` field if present, so the HIBP message would show through. However the 409 message `"A user with this email already exists."` would also show as-is, which is correct. This is acceptable but worth noting: if the Carddroper register page adopts the foodapp pattern verbatim, it inherits foodapp-specific copy like `"you@restaurant.com"` as the email placeholder.
**Recommendation:** When building the Carddroper register page, use `"you@example.com"` or `"you@company.com"` as the placeholder, not the foodapp restaurant-specific copy.

---

### F-8: Foodapp `register/page.tsx` sends `billing_name` and `stripe_payment_method_id` which the Carddroper backend does not accept

**Severity:** medium
**Frontend assumption (foodapp pattern):** `RegisterRequest` includes `billing_name?: string` and `stripe_payment_method_id?: string`. The foodapp's backend accepted these.
**Reality:** The Carddroper `RegisterRequest` schema (`auth.py:126`) only accepts `email`, `password`, `full_name`. Pydantic by default ignores extra fields (mode depends on config), so this likely would not 422. But it could depending on `model_config`. More importantly, if a Carddroper developer ports the foodapp register page without removing the Stripe fields, card data would silently go into the request body (wrong for security — card data must never touch our server).
**Recommendation:** When building the Carddroper register page, do NOT port the Stripe Elements section from foodapp. Carddroper registration does not require payment at signup (per current spec). Card fields in the register request body would be a security policy violation.

---

### F-9: `POST /auth/verify-email` backend returns 200 for both "verified" and "already verified" — UI cannot distinguish without reading the message string

**Severity:** low
**Frontend assumption (future):** A verify-email landing page will want to show different UI for "verified successfully" vs "already verified (maybe link re-used)."
**Reality:** Both cases return HTTP 200. The only differentiator is the `message` field string: `"Email verified."` vs `"Email already verified."`. Branching on message strings is fragile (subject to backend wording changes).
**Recommendation:** When building the verify-email page, branch on the exact message string for now but file a note that a 204 vs 200 distinction or a `{ "already_verified": bool }` field would be cleaner. This is a backend API design point, not a blocker — flag in 0011 brief.

---

### F-10: Success copy on forgot-password mentions "spam folder" but 0010's retry/timeout budget is ~36s worst-case

**Severity:** low
**Frontend assumption (foodapp pattern):** After submitting forgot-password, the UI immediately shows `"Check your inbox (and spam folder)."` — implies the email has been sent synchronously.
**Reality after 0010:** The backend sends email inline (synchronous to the HTTP request) with tenacity: up to 3 attempts at 1s → 4s → 16s backoff = ~21s max + per-attempt 5s timeout × 3 = worst case ~36 seconds. The HTTP request to `POST /auth/forgot-password` can take up to ~36s before it returns. The `"Sending..."` button label on submit is the only in-progress indicator; there is no spinner with a timeout or abort.
**Reality impact:** During a 36s worst case, the user sees a spinner (submit button disabled). If the user's browser has a shorter timeout (rare but possible on mobile with aggressive power management), the request may appear to hang. The success screen then says "check your inbox" implying immediate delivery — users may check within seconds and not find the email (since SendGrid deliver is async from the backend's perspective, but the backend wouldn't return 200 until after `send_email()` returned, so the email is "in flight" at that point, not "in inbox"). This is an expectation mismatch.
**Recommendation:** When building the Carddroper forgot-password page, add a note in copy like "Delivery may take a minute" rather than implying instant receipt. Technically out of scope for 0010 since the page doesn't exist yet; note for 0011.

---

### F-11: `apiFetch` in `lib/api.ts` does not wrap network errors — email-send failures that cause the backend to hang would show a raw `TypeError` in the UI

**Severity:** low (already noted as F-8 in ticket 0009 frontend audit)
**Relevance to 0010:** If the `POST /auth/forgot-password` request takes the full ~36s and then the connection is dropped (network blip), the frontend will catch a native `TypeError("Failed to fetch")` that the current `ApiError` instanceof check would miss. Any error UI in the future register/forgot-password pages that does `error instanceof ApiError` would silently swallow network errors.
**Recommendation:** Already flagged. Fold network-error wrapping into the first form ticket (0011). Noted here for completeness.

---

### F-12: `AuthResponse` returned by `POST /auth/register` contains `access_token` and `refresh_token` in the body in addition to cookies

**Severity:** nit
**Frontend assumption:** The foodapp auth context reads the `AuthResponse` body and caches user data, ignoring the raw token strings (it uses cookies for auth, not the body tokens). This is correct.
**Reality:** The Carddroper backend `AuthResponse` (`auth.py:150-153`) returns both `access_token` and `refresh_token` as body fields. The foodapp pattern correctly ignores these on web (cookies are authoritative). The Carddroper `UserResponse` includes `verified_at: Optional[datetime]` (new field vs foodapp's response shape, which had `restaurant` and `subscription`). Any Carddroper auth context ported from foodapp must handle the different `MeResponse` shape.
**Recommendation:** When building `context/auth.tsx` for Carddroper, type `UserResponse` to include `verified_at` and exclude foodapp-specific fields (`restaurant`, `subscription`). The Carddroper `GET /auth/me` returns `{ id, email, full_name, verified_at }` only.

---

## 3. Gaps

### G-1: No `/verify-email-sent` page
The route is named in `doc/systems/auth.md` but neither exists in the frontend nor in the foodapp pattern. This is a true original build, not a port. Must be in 0011 scope.

### G-2: No `/verify-email` landing page
Required to consume the verification link emailed by the backend. No pattern exists anywhere in the codebase to port. Must be in 0011 scope.

### G-3: No `/confirm-email-change` landing page
Required to consume the email-change confirmation link. The URL path is not specified in `doc/systems/auth.md` or `doc/reference/backend-api.md`. The confirm-email-change link target URL must be decided before ticket 0010's SendGrid templates are created in Phase 1 — the Dynamic Template must embed the correct URL.

### G-4: No change-email UI
`POST /auth/change-email` exists and works. No form to trigger it. Affects the user's ability to self-serve an email correction. Must be scoped into an account-settings ticket.

### G-5: No resend-verification UI
`POST /auth/resend-verification` exists and works. No button triggers it. The `/verify-email-sent` page (G-1) is the intended home for this CTA.

### G-6: No `email_send_failed` error handling on any frontend flow
The backend wraps all email sends in try/except and logs the failure but does NOT propagate it to the HTTP response — the endpoints return 200 even when email send fails. This is intentional (best-effort send). The frontend therefore has no way to detect email-send failure. There is no "email send failed" error code that any frontend handler needs to implement. This is by design and consistent after 0010 — the `send_email()` reshape does not change the best-effort contract.

### G-7: No `verified_at` awareness in the frontend
After a user registers, they are logged in (cookies set) but `verified_at` is null. Any future page that calls `GET /auth/me` will receive `verified_at: null`. The frontend has no `is_verified` derived state and no verification banner. This is a 0011 scope gap.

### G-8: `doc/reference/backend-api.md` does not have request/response detail for any endpoint
The API reference is a table of route stubs: no request schema, no response schema, no error conditions. This means future frontend developers must read `backend/app/routes/auth.py` directly to understand what to send and what to expect. The response shapes discovered in section 1 of this audit (e.g., `UserResponse` includes `verified_at`, `AuthResponse` contains both tokens and user) diverge from what the foodapp pattern assumes. This is a documentation gap, not a code bug.

---

## 4. Automated Checks Status

Both tools were available and executed successfully.

### `npm run lint`

```
> frontend@0.1.0 lint
> eslint .
```

**Result: PASS** — zero warnings, zero errors. Exit code 0.

### `npx tsc --noEmit`

**Result: PASS** — no output, exit code 0.

Both checks pass cleanly. The frontend scaffold contains only four source files with no complex logic, so these results are expected. They will be more meaningful once auth pages are added in ticket 0011.

---

## 5. Risk Index

The following are user-visible things that would mislead or break if ticket 0010 ships as written, in priority order:

1. **Dead verification link** (highest risk): 0010 configures SendGrid to send `VERIFY_EMAIL` template emails whose links point to `https://carddroper.com/verify-email?token=<jwt>`. This route does not exist in the frontend. Every user who clicks "verify your email" lands on a Next.js 404 page. Impact: 100% of new sign-ups are stuck unverified until 0011 ships.

2. **Dead email-change confirmation link**: `CHANGE_EMAIL` template emails will contain a confirmation link. The target URL path is not even defined in the docs. No frontend route exists to handle it. Impact: the change-email feature is completely non-functional end-to-end even after the backend is deployed.

3. **No verify-email-sent screen**: After `POST /auth/register` succeeds, the frontend has nowhere sensible to send the user. The intended UX (`/verify-email-sent` + resend button) does not exist. Without it, users have no self-service path if the verification email lands in spam.

4. **~36-second worst-case request latency with no UX accommodation**: `POST /auth/forgot-password` can block for up to ~36 seconds under 0010's retry budget. The submit-button spinner is the only indicator. No timeout messaging, no "still working..." transition, no abort CTA. Users on slow connections or with aggressive browser timeouts may see the page appear frozen.

5. **Foodapp error handling patterns are too generic for Carddroper's error codes**: Rate-limit errors (429) on forgot-password show `"Something went wrong. Please try again."` — which invites the user to retry immediately, hitting the rate limit again. When building Carddroper's auth pages, the error handling must be improved.

6. **`AuthResponse` shape mismatch**: The foodapp auth context (`lib/auth.tsx`) caches `{ id, email, full_name, restaurant, subscription }` from `POST /auth/register` / `POST /auth/login`. The Carddroper backend returns `{ id, email, full_name, verified_at }` — no `restaurant`, no `subscription`. A naive port of the foodapp auth context would have TypeScript errors (which strict mode will catch) and runtime shape mismatches.
