---
id: 0015
title: email verification flow — frontend foundations + register/login/verify pages
status: resolved
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
- `doc/architecture/site-model.md` — **DECIDED 2026-04-22.** Canva-model auth wall with chassis/body split. Authoritative for page structure and middleware scope in this ticket.
- `doc/systems/auth.md` — token strategy, cookie delivery, 7-day lock, verification UX
- `doc/architecture/overview.md` — request flow, where auth state lives
- `doc/reference/backend-api.md` — endpoint catalogue (all target endpoints exist)
- `doc/audits/2026-04-21-frontend-pre-0014-audit.md` — F-1, F-2, F-6, F-7 all fold in here
- `doc/operations/testing.md` — per-ticket coverage checklist applies
- foodapp pattern source: `/Users/johnxing/mini/foodapp/frontend/src/lib/api.ts`
  (401-refresh interceptor), `context/auth.tsx` (auth context), `middleware.ts`
  (edge route protection) — port patterns, don't copy verbatim. **Note:** Next.js 16
  renamed `middleware.ts` → `proxy.ts` (see https://nextjs.org/docs/messages/middleware-to-proxy);
  this ticket uses `proxy.ts`. Foodapp is on Next.js 15 and still uses the old name.

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
- F-7 medium — `proxy.ts` (Next.js 16 rename of `middleware.ts`) + `(marketing)` / `(auth)` / `(app)` route groups

## Scope

**In scope — chassis foundations + MVP email-verification flow:**
- Frontend auth plumbing (api wrappers, 401 interceptor, auth context, middleware).
- Three route groups per `site-model.md`: `(marketing)/`, `(auth)/`, `(app)/`.
- `config/brand.ts` — single-source brand constants (`name`, `domain`, `fromEmail`, `supportEmail`).
- `/` (marketing landing) — public. Auth-aware header: Sign in / Register for anon; `{email}` + Logout for authed.
- `/register` — create account, triggers verification email, redirects to `/verify-email-sent`.
- `/login` — existing-user login; on success → `/app`.
- `/verify-email-sent` — post-register landing. Shows "check your inbox" + "resend email" button (rate-limited via backend).
- `/verify-email?token=…` — consumes token, flips `verified_at`, redirects to `/login` (verification increments `token_version` → user must re-authenticate per auth.md).
- `/app` — auth-gated stub. "You're logged in as `{email}`" + Logout button. Unverified users can reach it (per auth.md §Soft cap: days 0–6).

**Explicitly out of scope (own tickets later):**
- `/forgot-password`, `/reset-password` pages → ticket 0016.
- Change-email settings page → ticket 0017.
- Legal acceptance checkbox + `/legal/terms` and `/legal/privacy` static pages → own ticket before first Stripe charge.
- Real marketing content (hero copy, features page, pricing page) — design ticket, post-MVP.
- Real `(app)/` body (card browser, customizer, send flow) — later Carddroper tickets.
- Verification gating modal — no gated actions ship in 0015, so no modal yet.
- Design system / shared form components beyond what naturally factors out.
- Analytics, Sentry, RUM.

## Design decisions (pre-committed so the agent doesn't paralyze)

- **Forms:** React Hook Form + Zod. Zod schemas double as client validation and TypeScript types (infer via `z.infer<>`).
- **Styling:** Tailwind v4 utility classes directly. No component library. When a pattern repeats ≥2 times, extract to `components/` — don't pre-factor.
- **Data fetching:** `@tanstack/react-query` v5 (already installed). Queries for reads, mutations for writes. Invalidate `['auth', 'me']` on login/register/logout/verify success.
- **Auth state:** single React Query `useQuery({ queryKey: ['auth', 'me'], queryFn: () => api.get('/auth/me'), staleTime: 30_000, retry: false })` — exposed through `useAuth()` hook returning `{ user, isLoading, isAuthenticated, isVerified }`. `retry: false` on this query (override the global `retry: 1`) so logged-out users don't pay a retry round-trip.
- **Access token storage:** HttpOnly cookie set by backend (already working). Frontend does not see or store access tokens in JS. Session "has logged in" signal lives in `sessionStorage` (`HAS_SESSION_KEY`) so anonymous visits don't trigger refresh attempts. Set on successful login/register, cleared on logout/401-after-refresh-fail.
- **Middleware:** cookie presence check only (HttpOnly means it can't decode). Gates `(app)/*` paths → redirect to `/login` if no `access_token` cookie. Also: authed user on `/login` or `/register` → redirect to `/app`. `(marketing)/*` and `(auth)/*` are otherwise public. Token validity confirmed by `GET /auth/me` in `AuthProvider`.
- **Route groups (per `site-model.md`):**
  - `app/(marketing)/` — `/` landing. Public. Layout renders auth-aware header but does not redirect either way.
  - `app/(auth)/` — `/login`, `/register`, `/verify-email-sent`, `/verify-email`. Public entry; authed-redirect handled by middleware (not layout) to keep the boundary in one place.
  - `app/(app)/` — `/app` stub. Middleware guarantees a cookie is present before layout runs; layout uses `useAuth()` to surface `{email}` and Logout.
  - Existing `app/page.tsx` (`<h1>Carddroper</h1>`) moves into `(marketing)/page.tsx` as the marketing-landing stub.
- **Error surface:** every form captures `ApiError` on mutation failure, renders the backend `error.message` as a form-level error. Zod handles field-level validation before the request fires. `code === "NETWORK_ERROR"` → show a generic retry prompt.
- **Success surface:** after register → `markLoggedIn()` + `invalidateQueries(['auth','me'])` + `router.push('/verify-email-sent')`. After login → same + `router.push('/app')`. After verify → `markLoggedOut()` (token_version bumped, access token invalidated) + user clicks the inline "Log in" button to go to `/login`. No toast system in 0015 — all success feedback is inline on the page.

## Phases

### Phase 0 — Chassis foundations (dispatch frontend-builder — separate dispatch from Phase 1)

**Reliability rationale for the split:** Phase 0 establishes the chassis boundary (route groups + middleware + auth plumbing) that every subsequent page sits on top of. If any of it is wrong, catching it before building five pages is cheaper. Ship Phase 0, verify the boundary in isolation (curl-driven), then dispatch Phase 1 with a known-good foundation.

Deliverables:
1. **`config/brand.ts`** — `export const brand = { name: "Carddroper", domain: "carddroper.com", fromEmail: "noreply@carddroper.com", supportEmail: "support@carddroper.com" } as const;` Single-source for brand strings used across auth-page copy, email templates, and marketing layout. Per `site-model.md` §Reusability constraint 2.
2. **`lib/api.ts` — typed wrappers.** Add `api.get<T>(path)`, `api.post<T>(path, body?)`, `api.patch<T>(path, body?)`, `api.put<T>(path, body?)`, `api.delete<T>(path)`. Each is a thin wrapper over `apiFetch` with the right `method` set. 204 handling uses `Promise<void>` on `delete` / `put` without return. This subsumes audit F-6 (204 cast nit).
3. **`lib/api.ts` — 401 silent-refresh interceptor** (audit F-1). On any non-login-path 401, attempt `POST /auth/refresh` exactly once (deduplicate concurrent 401s via a module-level `refreshPromise`). Retry the original request on refresh success. On refresh failure, clear the `HAS_SESSION_KEY` sessionStorage flag and throw the original 401. Refresh-exempt paths: `/auth/refresh`, `/auth/login`, `/auth/register`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/verify-email`. Do not attempt refresh if `HAS_SESSION_KEY` is not set (skip the round-trip for truly anonymous visits).
4. **`context/auth.tsx` — `AuthProvider` + `useAuth`** (audit F-2). Provider wraps children with a React Query `['auth', 'me']` query (`retry: false`, `staleTime: 30_000`). Hook returns `{ user, isLoading, isAuthenticated, isVerified, markLoggedIn, markLoggedOut }`. `markLoggedIn` sets `HAS_SESSION_KEY` and invalidates the query. `markLoggedOut` clears it and resets the query. Nest `AuthProvider` inside `QueryClientProvider` in `app/providers.tsx`.
5. **`proxy.ts`** (audit F-7; Next.js 16 rename of `middleware.ts`). Single source of auth routing. File lives at `frontend/proxy.ts` (repo root of frontend, not under `app/`). Export function is `proxy` (not `middleware`). Runs on Node.js runtime (Edge is not supported by `proxy`; acceptable for a cookie-presence check). See https://nextjs.org/docs/app/api-reference/file-conventions/proxy.
   - Path starts with `/app` and no `access_token` cookie → 307 to `/login`.
   - Path is `/login` or `/register` and `access_token` cookie present → 307 to `/app`.
   - Otherwise pass through.
   - Export a `config` object with a `matcher` that excludes `/_next/*`, `/favicon.ico`, and static assets so the middleware does NOT run on every asset request. Use the standard Next.js 16 negative-lookahead pattern:
     ```typescript
     export const config = {
       matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)).*)"],
     };
     ```
6. **Route groups + layouts.**
   - Create `app/(marketing)/layout.tsx` with an auth-aware header (reads `useAuth()`): anon → "Sign in" / "Register" links; authed → `{email}` + Logout button. Move existing `app/page.tsx` (`<h1>Carddroper</h1>`) to `app/(marketing)/page.tsx`.
   - Create `app/(auth)/layout.tsx` — minimal centered container; no auth redirects (middleware handles them).
   - Create `app/(app)/layout.tsx` — minimal; uses `useAuth()` to expose email + Logout button in a thin header. Create `app/(app)/app/page.tsx` with the stub body: "You're logged in as `{user.email}`." + Logout.
7. **Dependencies added:** `react-hook-form`, `@hookform/resolvers`, `zod`.

Phase 0 acceptance:
- `npm run lint` / `npx tsc --noEmit` / `npm run build` clean.
- `docker build ./frontend` succeeds.
- Middleware behavior verified with `curl -I` (no cookie on `/app` → 307 `/login`; cookie on `/login` → 307 `/app`; no cookie on `/` → 200).
- Anonymous visit to `/` does NOT trigger a `POST /auth/refresh` (HAS_SESSION_KEY gate works).
- `/app` stub renders `<h1>Carddroper</h1>` + email + Logout for a verified user logged in against docker-compose backend.

### Phase 1 — Pages (dispatch frontend-builder after Phase 0 verified)

Deliverables:
1. **`app/(auth)/register/page.tsx`** — email + password + confirm-password. Zod validation (email format, password ≥ 10 chars). On submit: `api.post('/auth/register', …)` → on success: `markLoggedIn()`, `invalidateQueries(['auth','me'])`, `router.push('/verify-email-sent')`. On 409 (email exists) → form-level error. On 422 (weak password) → field-level error. No ToS checkbox in 0015 (deferred to legal-acceptance ticket).
2. **`app/(auth)/login/page.tsx`** — email + password. Same patterns. On success → `markLoggedIn()` + `invalidateQueries(['auth','me'])` + `router.push('/app')`. On 401 → form-level "invalid credentials" (don't disambiguate email-exists vs wrong-password, per anti-enumeration). On 429 → rate-limit message with retry-after if the backend surfaces it. No "forgot password" link in 0015 (deferred to 0016).
3. **`app/(auth)/verify-email-sent/page.tsx`** — "We sent a verification email to `{user.email}`. Click the link to verify your account." Shows a "Resend email" button that calls `api.post('/auth/resend-verification')`. Button is disabled during pending mutation. On 200, swap the button with an inline success message: "Verification email sent — check your inbox." On 429, swap with inline message: "Please wait before requesting another email." Requires an authenticated session to read `user.email` — if unauthed (direct hit, no `HAS_SESSION_KEY`), render a fallback "Check your inbox; if you don't have an account, [register]." No redirect; the page is informational. **No toast system is introduced in 0015** — inline feedback only.
4. **`app/(auth)/verify-email/page.tsx`** — reads `?token=…` from searchParams. On mount, calls `api.post('/auth/verify-email', { token })`. **Backend response shapes (authoritative, from `backend/app/routes/auth.py`):**
   - 200 OK `{ "message": "Email verified." }` — newly verified (this call set `verified_at` and bumped `token_version`).
   - 200 OK `{ "message": "Email already verified." }` — idempotent path; user was already verified.
   - 401 Unauthorized — invalid/expired token, or token's `sub` resolves to no user (deferred edge, see §deferrals).
   - 422 Unprocessable — malformed token payload (non-string token, missing fields).

   **Frontend states (pages must handle each):**
   - **pending** — spinner + "Verifying your email…" centered.
   - **success (200, either message)** — inline success panel: "Your email is verified. Please log in to continue." + primary button to `/login`. Call `markLoggedOut()` to clear `HAS_SESSION_KEY` — newly-verified users had their `token_version` bumped and their cookie is now dead; already-verified users reaching this page must also re-auth. Do NOT auto-redirect; let the user click the button. (Auto-redirect with a timer creates race conditions with toast-style feedback that we're deliberately not shipping.)
   - **401 / 422** — treat identically: inline error panel: "This verification link is invalid or expired." + primary button to `/verify-email-sent` labeled "Request a new email" + secondary link to `/login`.
   - **network error** (`code === "NETWORK_ERROR"`) — inline retry panel with a retry button that re-runs the mutation.

   Guard against the React 19 strict-mode double-mount firing the mutation twice. The verify-email endpoint is idempotent on the second call (returns 200 "already verified") so this is safe, but use React Query's `useMutation` with a one-shot `useEffect(() => mutate({ token }), [])` pattern — not in-render-side-effects.
5. **Tiny helper components that fall out naturally** — `<FormField>`, `<SubmitButton>`, `<FormError>`. Extract to `components/` only if used in ≥ 2 pages. Don't pre-factor.

Phase 1 acceptance: each page renders; forms validate; mutations fire; error and success states render for each page. Lint / tsc / build clean. Local docker-compose golden path works end-to-end against the backend's dev email fallback (the `dev_preview_url` in the backend logs gives the verify URL to click). End-to-end flow: `/` → Register → `/register` → submit → `/verify-email-sent` → click dev preview URL → `/verify-email` → success → `/login` → submit → `/app`.

### Phase 2 — Staging smoke (user)

1. Merge `dev` → `main`, push, wait for Cloud Build SUCCESS.
2. Run existing smokes:
   - `.venv/bin/python backend/scripts/smoke_healthz.py`
   - `.venv/bin/python backend/scripts/smoke_auth.py` (confirms backend golden path still works)
3. Write a new smoke: `backend/scripts/smoke_verify_email.py` — registers a `smoke+<uuid>@carddroper.com` user against staging, asserts the register response, then asserts the verify-email-sent / resend-verification round-trip at the API level (not through the browser). The end-to-end browser flow is user-manual for v0.1.
4. Manual browser check on `https://staging.carddroper.com`:
   - Register with a personal inbox you control (not `@carddroper.com`) → land on `/verify-email-sent`.
   - Receive the verification email (real SendGrid delivery — DKIM should pass).
   - Click the link → land on `/verify-email` → success state renders → click "Log in" → land on `/login`.
   - Log in with the just-verified credentials → redirect to `/app` → `/app` stub shows `{email}` + Logout.
   - Sign out → header swaps to anon state → `/app` now redirects to `/login`.
   - `/auth/me` should return `verified_at` non-null after the verify click (inspect in devtools or a subsequent login session).

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

Post-0014.5 surviving deferrals:
- **Backend F-3** (`users.updated_at` best-effort) — revisit when the first bulk UPDATE route lands. None in 0015 or 0016. Owned inline when a route surfaces.
- **Frontend F-9** (`.dockerignore *.md` nit) — opportunistic, no ticket. Next time `.dockerignore` is touched for any other reason.
- **`verify-email` user-not-found-after-decode 401 path** — flagged during 0014 Phase 0. **May surface in Phase 1 here.** If the 401 silent-refresh interceptor loops on this 401 during real verify-email UX testing, change the backend to 422 for consistency and add a regression test in this ticket. If the interceptor correctly scopes to non-auth routes (which it should — `/auth/verify-email` is on the refresh-exempt list), keep deferred. Decision made when Phase 1 lands.

Scheduled elsewhere:
- `/forgot-password` + `/reset-password` → ticket 0016.
- `/change-email` + email-changed flow → ticket 0017.
- Legal acceptance checkbox + `/legal/terms` + `/legal/privacy` → own ticket before first Stripe charge.

Resolved pre-0015 (for traceability):
- 0009 F-2 (viewport), 0009 F-6 (tsconfig tightening), Backend F-6 (`sleep 3` → socket probe), Dockerfile-COPY postmortem → all fixed in 0014.5 (commits `48faec7`, `c0779f5`).

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

Closed 2026-04-22. Golden-path walkthrough green on staging end-to-end.

**What shipped:**
- **Phase 0 chassis** (commit `ffac324`): three route groups `(marketing)/(auth)/(app)`, `frontend/proxy.ts` cookie-presence auth gate (Next.js 16 rename of `middleware.ts`), `useAuth` + `AuthProvider`, typed `api.get/post/...` wrappers, 401 silent-refresh with `HAS_SESSION_KEY` anonymous gate, `config/brand.ts`, `scripts/smoke_chassis.sh` (17-assertion end-to-end smoke).
- **Phase 1 pages** (commit `c26498f`): `/register` (RHF+Zod, 409/422/network handling), `/login` (anti-enumeration on 401, rate-limit on 429), `/verify-email-sent` (useAuth-gated Resend), `/verify-email?token=` (useSearchParams wrapped in Suspense, useRef guard for React 19 strict-mode double-mount). Helpers extracted at `components/forms/`: FormField, FormError, SubmitButton.
- **Staging smoke** (commit `b1bad78`): `backend/scripts/smoke_verify_email.py` covering register + `/auth/me` + resend-verification + logout at API level. Verify-token click is manual-only (no public token-mint).

**Follow-ups that landed inside this epic:**
- `0015.5` — CORS chassis guard + first `chassis-contract.md` entry (the Option C coupling rule established here).
- `0015.6` — cross-subdomain cookie chassis invariant (`COOKIE_DOMAIN`) + Resend-button auth guard. Unblocked the staging browser flow.
- `0015.7` — verify-email clears dead cookies. **Superseded by 0015.8** after user feedback: the underlying spec (force re-login on verify) was an industry-minority position with marginal security gain and real UX friction.
- `0015.8` — verify-email is a capability toggle. Dropped `token_version` bump + `revoke_all_user_tokens` from the verify-email endpoint; frontend success panel redirects to `/app` instead of `/login`. Matches the session-preserving convention of GitHub/Linear/Notion/Stripe/Canva.

**Staging validation (2026-04-22):**
- All four smokes green: `smoke_healthz`, `smoke_auth --expected-cookie-domain=.staging.carddroper.com`, `smoke_verify_email`, `smoke_cors`.
- Manual browser walkthrough — all steps pass: register → `/verify-email-sent`, real SendGrid delivery, click verify link → success panel → Continue → `/app` still logged in with `verified_at` set. Logout → proxy re-gates `/app`. Authed user on `/login` or `/register` redirects to `/app`. Re-click stale verify link → idempotent 200. Invalid token → error panel, no mutation.

**Deferred, tracked elsewhere:**
- `0016` forgot/reset password pages.
- `0017` change-email flow.
- `0018` chassis-hardening audit (JWT_SECRET strength, SENDGRID-required-when-sandbox-off, DATABASE_URL required in prod, etc.).
- `0019` email deliverability (walkthrough step 1.3: verification email landed in spam). SendGrid Sender Authentication + SPF/DKIM/DMARC. User-owned DNS + SendGrid console work.
- `0020` legal acceptance: ToS checkbox on `/register`, static `/legal/terms` + `/legal/privacy` pages. Gates first Stripe charge.
- Backend audit F-3 (`users.updated_at` best-effort) — trigger: first bulk UPDATE route lands. Owned inline then. Still deferred.
- Frontend audit F-9 (`.dockerignore *.md` nit) — trigger: next time `.dockerignore` is touched. Still deferred.

**Retired (deferral closed, not worth a ticket):**
- `verify-email` user-not-found-after-decode 401 path (originally flagged in 0014 audit). The backend returns 401 when the decoded token's `sub` has no matching user and 422 for undecodable tokens. The original concern was (a) API symmetry (both conditions are "bad token") and (b) a potential refresh loop. Both are moot: frontend treats 401 and 422 identically in the invalid-link panel, `/auth/verify-email` is on the refresh-exempt list, and 0015.8 removed the session-invalidation policy so there is no tv-mismatch loop to worry about. Closed as wontfix; do not re-open without new evidence.

**Lessons captured in chassis:**
- `chassis-contract.md` pattern + CLAUDE.md coupling rule (established 0015.5; extended 0015.6).
- `scripts/smoke_chassis.sh` env-var-parameterized for reuse across future chassis-based projects.
- Option C doc-coupling policy: every Settings validator lands its contract entry in the same commit.
