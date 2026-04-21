---
id: 0015
title: email verification flow — frontend foundations + register/login/verify pages
status: open
priority: high
found_by: PLAN.md §10.5 remainder; pre-0015 frontend audit 2026-04-21
---

## Context

Backend verification infrastructure landed under ticket 0010. The remaining §10.5
work is the **frontend verification flow** — the pages a user actually uses to
create an account, receive a verification email, click the link, and land in a
verified state. This ticket builds those pages plus the foundational frontend
auth plumbing they need.

Grounded in:
- `doc/systems/auth.md` — token strategy, cookie delivery, 7-day lock, verification UX
- `doc/architecture/overview.md` — request flow, where auth state lives
- `doc/reference/backend-api.md` — endpoint catalogue (all target endpoints exist)
- `doc/audits/2026-04-21-frontend-pre-0014-audit.md` — F-1, F-2, F-6, F-7 all fold in here
- `doc/operations/testing.md` — per-ticket coverage checklist applies
- foodapp pattern source: `/Users/johnxing/mini/foodapp/frontend/src/lib/api.ts`
  (401-refresh interceptor), `context/auth.tsx` (auth context), `middleware.ts`
  (edge route protection) — port patterns, don't copy verbatim

**Backend state (per audit, already green):** `create_verify_token`,
`decode_verify_token`, `POST /auth/verify-email`, `POST /auth/resend-verification`
(3/hour rate-limited), 24h TTL, single-use via `token_version` bump,
`send_email(EmailTemplate.VERIFY_EMAIL, ...)` wired at register and resend,
dev fallback emits `dev_preview_url` with the token URL. No backend work
expected in this ticket — flag if any surfaces.

**Deferred from the 2026-04-21 frontend audit (folding in here):**
- F-1 high — 401 silent-refresh interceptor in `apiFetch`
- F-2 high — auth context + `useAuth` + `GET /auth/me` query
- F-6 low — typed `api.get/post/delete` wrappers (subsumes the 204 cast nit)
- F-7 medium — `middleware.ts` + `(auth)` / `(dashboard)` route groups

## Scope

**In scope — MVP email-verification flow:**
- Frontend auth plumbing (api wrappers, 401 interceptor, auth context, middleware + route groups).
- `/register` — create account, triggers verification email, redirects to `/verify-email-sent`.
- `/login` — existing-user login; post-login routing depends on `verified_at`.
- `/verify-email-sent` — post-register / logged-in-unverified landing. Shows "check your inbox" + a "resend email" button (rate-limited via backend).
- `/verify-email?token=…` — consumes token, flips `verified_at`, routes to `/` on success.
- `/` (root) — auth-aware: unauthed → marketing stub; authed-unverified → redirect to `/verify-email-sent`; authed-verified → stub dashboard.

**Explicitly out of scope (own tickets later):**
- `/forgot-password`, `/reset-password` pages → ticket 0016.
- Change-email settings page → ticket 0017+.
- Logout UI affordance beyond a bare button on the dashboard stub.
- Real dashboard content — a stub page that says "Welcome, {email}" is sufficient.
- Design system / shared form components beyond what naturally factors out.
- Analytics, Sentry, RUM.

## Design decisions (pre-committed so the agent doesn't paralyze)

- **Forms:** React Hook Form + Zod. Zod schemas double as client validation and TypeScript types (infer via `z.infer<>`).
- **Styling:** Tailwind v4 utility classes directly. No component library. When a pattern repeats ≥2 times, extract to `components/` — don't pre-factor.
- **Data fetching:** `@tanstack/react-query` v5 (already installed). Queries for reads, mutations for writes. Invalidate `['auth', 'me']` on login/register/logout/verify success.
- **Auth state:** single React Query `useQuery({ queryKey: ['auth', 'me'], queryFn: () => api.get('/auth/me'), staleTime: 30_000, retry: false })` — exposed through `useAuth()` hook returning `{ user, isLoading, isAuthenticated, isVerified }`. `retry: false` on this query (override the global `retry: 1`) so logged-out users don't pay a retry round-trip.
- **Access token storage:** HttpOnly cookie set by backend (already working). Frontend does not see or store access tokens in JS. Session "has logged in" signal lives in `sessionStorage` (`HAS_SESSION_KEY`) so anonymous visits don't trigger refresh attempts. Set on successful login/register, cleared on logout/401-after-refresh-fail.
- **Middleware:** cookie presence check only (HttpOnly means it can't decode). Enough to redirect `/dashboard` routes to `/login` if no cookie. Actual validity confirmed by `GET /auth/me` in `AuthProvider`.
- **Route groups:**
  - `app/(auth)/` — `/login`, `/register`, `/verify-email-sent`, `/verify-email`. Layout redirects *authenticated-verified* users to `/` (the dashboard stub).
  - `app/(dashboard)/` — `/` (post-auth landing). Layout redirects *unauthenticated* users to `/login`; *unverified* users to `/verify-email-sent`.
  - Root `app/page.tsx` moves into `(dashboard)/page.tsx`. A public marketing landing can come later.
- **Error surface:** every form captures `ApiError` on mutation failure, renders the backend `error.message` as a form-level error. Zod handles field-level validation before the request fires. `code === "NETWORK_ERROR"` → show a generic retry prompt.
- **Success surface:** after register, `queryClient.invalidateQueries(['auth', 'me'])` + `router.push('/verify-email-sent')`. After verify, same + push to `/`. After login, same + push to `/` (unverified users will bounce through the middleware to `/verify-email-sent`).

## Phases

### Phase 0 — Frontend foundations (dispatch frontend-builder)

Lands the plumbing. Nothing user-visible yet.

Deliverables:
1. **`lib/api.ts` — typed wrappers.** Add `api.get<T>(path)`, `api.post<T>(path, body?)`, `api.patch<T>(path, body?)`, `api.put<T>(path, body?)`, `api.delete<T>(path)`. Each is a thin wrapper over `apiFetch` with the right `method` set. 204 handling uses `Promise<void>` on `delete` / `put` without return. This subsumes audit F-6 (204 cast nit).
2. **`lib/api.ts` — 401 silent-refresh interceptor** (audit F-1). On any non-login-path 401, attempt `POST /auth/refresh` exactly once (deduplicate concurrent 401s via a module-level `refreshPromise`). Retry the original request on refresh success. On refresh failure, clear the `HAS_SESSION_KEY` sessionStorage flag and throw the original 401. Refresh-exempt paths: `/auth/refresh`, `/auth/login`, `/auth/register`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/verify-email`. Do not attempt refresh if `HAS_SESSION_KEY` is not set (skip the round-trip for truly anonymous visits).
3. **`context/auth.tsx` — `AuthProvider` + `useAuth`** (audit F-2). Provider wraps children with a React Query `['auth', 'me']` query. Hook returns `{ user, isLoading, isAuthenticated, isVerified, markLoggedIn, markLoggedOut }`. `markLoggedIn` sets `HAS_SESSION_KEY` and invalidates the query. `markLoggedOut` clears it and resets the query. Nest `AuthProvider` inside `QueryClientProvider` in `app/providers.tsx`.
4. **`middleware.ts`** (audit F-7). Check cookie presence; redirect `/` (and any future `/(dashboard)` routes) to `/login` if no `access_token` cookie. Auth'd-accessing-(auth) page redirection is handled in the `(auth)` layout, not middleware, because middleware can't know verification state without decoding the token.
5. **Route groups.** Create `app/(auth)/layout.tsx` (redirects verified users to `/`) and `app/(dashboard)/layout.tsx` (redirects unauthed to `/login`, unverified to `/verify-email-sent`). Move existing `app/page.tsx` (`<h1>Carddroper</h1>`) to `app/(dashboard)/page.tsx` as the post-login stub.
6. **Dependencies added:** `react-hook-form`, `@hookform/resolvers`, `zod`.

Phase 0 acceptance: lint / tsc / build clean; `HAS_SESSION_KEY` round-trip works locally against docker-compose backend (anonymous visit → no refresh attempt; logged-in → refresh attempt on expiry); dashboard stub shows `<h1>Carddroper</h1>` for a verified user, middleware redirects to `/login` otherwise.

### Phase 1 — Pages (dispatch frontend-builder, same dispatch or split — your call)

Deliverables:
1. **`app/(auth)/register/page.tsx`** — email + password + confirm-password. Zod validation (email format, password ≥ 10 chars). On submit: `api.post('/auth/register', …)` → on success: `markLoggedIn()`, `invalidateQueries(['auth','me'])`, `router.push('/verify-email-sent')`. On 409 (email exists) → form-level error. On 422 (weak password) → field-level error.
2. **`app/(auth)/login/page.tsx`** — email + password. Same patterns. On 401 → form-level "invalid credentials" (don't disambiguate email-exists vs wrong-password, per anti-enumeration). On 429 → rate-limit message with retry-after if the backend surfaces it.
3. **`app/(auth)/verify-email-sent/page.tsx`** — "We sent a verification email to `{user.email}`. Click the link to verify your account." Shows a "Resend email" button that calls `api.post('/auth/resend-verification')`. Button is disabled during pending mutation and shows a success toast on 200. On 429, show "please wait before requesting another email." This page requires authentication; unverified users land here naturally via middleware, and `(auth)/layout.tsx` redirects *verified* users away to `/`.
4. **`app/(auth)/verify-email/page.tsx`** — reads `?token=…` from searchParams. On mount, calls `api.post('/auth/verify-email', { token })`. States: pending (spinner), success ("Verified! Redirecting…" + `router.push('/')` after 2s + invalidate auth/me), 422 ("This verification link is invalid or expired. Try requesting a new one." with button to `/verify-email-sent`), 401 (subject user missing — treat same as 422 for UX), 429 (already verified — show "Your email is already verified" + link to `/`).
5. **Tiny helper components that fall out naturally** — `<FormField>`, `<SubmitButton>`, `<FormError>`. Extract to `components/` only if used in ≥ 2 pages. Don't pre-factor.

Phase 1 acceptance: each page renders; forms validate; mutations fire; error and success states render for each page. Lint / tsc / build clean. Local docker-compose golden path works end-to-end against the backend's dev email fallback (the `dev_preview_url` in the backend logs gives the verify URL to click).

### Phase 2 — Staging smoke (user)

1. Merge `dev` → `main`, push, wait for Cloud Build SUCCESS.
2. Run existing smokes:
   - `.venv/bin/python backend/scripts/smoke_healthz.py`
   - `.venv/bin/python backend/scripts/smoke_auth.py` (confirms backend golden path still works)
3. Write a new smoke: `backend/scripts/smoke_verify_email.py` — registers a `smoke+<uuid>@carddroper.com` user against staging, asserts the register response, then asserts the verify-email-sent / resend-verification round-trip at the API level (not through the browser). The end-to-end browser flow is user-manual for v0.1.
4. Manual browser check on `https://staging.carddroper.com`:
   - Register with a personal inbox you control (not `@carddroper.com`).
   - Receive the verification email (real SendGrid delivery — DKIM should pass).
   - Click the link → land on `/verify-email` → success state → `/`.
   - Sign out, sign back in — session persists, `/auth/me` returns `verified_at` non-null, no redirect to `/verify-email-sent`.

## Verification

**Automated checks (agent, reported in Phases 0 and 1):**
- `npm run lint` — zero.
- `npx tsc --noEmit` — zero.
- `npm run build` — succeeds.
- `docker build ./frontend` — succeeds.
- Every page renders without runtime error on the local dev server.
- Form validation triggers on bad input (manual check documented in the Report).

**Functional smoke (user, Phase 2):**
- All three existing smokes green post-deploy.
- New `smoke_verify_email.py` green (`SMOKE OK: verify_email`).
- Manual browser register → verify → signed-in flow works end-to-end on staging.

## Out of scope — tracked deferrals

From the 2026-04-21 audits and earlier:
- **Backend F-3** (`users.updated_at` best-effort) — revisit when first filter on `updated_at` lands.
- **Backend F-6** (`sleep 3` in `cloudbuild.yaml` migrate step) — revisit on first intermittent failure in staging.
- **Frontend F-9** (`.dockerignore *.md` nit) — no action unless frontend `README.md` needs to ship.
- **0009 F-2** (viewport export on layout) — add on next layout touch.
- **0009 F-6** (`noUncheckedIndexedAccess` / `noImplicitOverride` tsconfig tightening) — separate maintenance ticket when the suite is mature enough to absorb the fallout.
- **`verify-email` user-not-found-after-decode 401 path** — flagged during 0014 Phase 0. If the 422-vs-401 distinction surfaces in frontend error handling here, change the backend to 422 for consistency and add a regression test. Otherwise leave.
- **Per-ticket checklist item: when deleting the last file in a build-context directory, check the Dockerfile for `COPY <dir>` references.** Surfaced by the 0014 Phase 3 hotfix. Add to `doc/operations/testing.md` §Per-ticket checklist when this ticket lands or earlier — orchestrator call.

## Report

Phase 0 (frontend-builder):
- Files added / modified with one-line what-changed each.
- Deps added to `package.json`.
- `npm run lint`, `npx tsc --noEmit`, `npm run build` output summaries.
- Any backend gap surfaced (should be none per audit — flag if not).
- Screenshots or terminal proof of middleware redirect behavior (curl with and without cookie to show the 307).
- Deviations.

Phase 1 (frontend-builder):
- Same build/lint/typecheck summaries.
- Per-page summary: what renders, what mutations fire, which error codes are handled.
- Shared components extracted (if any) + their locations.
- Local docker-compose end-to-end walk-through proof (curl or screenshot).
- Deviations.

Phase 2 (user):
- Cloud Build SUCCESS link.
- Three smoke outputs + `smoke_verify_email.py` output.
- Screenshot or timestamp of real-inbox verification email receipt.

## Resolution

*(filled in by orchestrator on close)*
