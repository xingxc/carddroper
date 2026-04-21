# Frontend Pre-0014 Audit — 2026-04-21

**Auditor:** frontend-builder agent
**Scope:** Full `frontend/` tree, read-only. Pre-ticket-0014 checkpoint.
**Prior closed findings excluded:** 0009 F-3 (SVGs) and F-7 (HOSTNAME) closed by ticket 0012. Not reopened.

---

## 1. Scope and Method

**Paths audited (all non-generated, non-node_modules):**

- `frontend/app/` — all `.tsx` / `.css` files
- `frontend/lib/` — all `.ts` files
- `frontend/hooks/` — directory exists, empty
- `frontend/components/` — directory exists, empty
- `frontend/public/` — static assets
- `frontend/next.config.ts`, `frontend/next.config.ts`
- `frontend/tailwind.config.ts` (absent — expected for v4)
- `frontend/postcss.config.mjs`
- `frontend/package.json`, `frontend/package-lock.json`
- `frontend/tsconfig.json`
- `frontend/Dockerfile`
- `frontend/.dockerignore`
- `frontend/eslint.config.mjs`
- `frontend/.env.example`

**Reference reads:**
- `doc/systems/auth.md` — token strategy, cookie delivery, verification flow
- `doc/architecture/tech-stack.md` — stack decisions
- `doc/architecture/overview.md` — request flow
- `doc/reference/backend-api.md` — endpoint catalogue
- `doc/audits/2026-04-20-frontend-audit.md` — prior findings
- `doc/issues/0009-code-audit.md` — dispositions from prior audit
- `doc/issues/0012-dockerfile-hardening.md` — what 0012 actually delivered
- `/Users/johnxing/mini/foodapp/frontend/src/lib/api.ts` — pattern source for 401-retry / session tracking

**Commands run (all in `frontend/`):**

| Command | Result |
|---|---|
| `npm run lint` | PASS — zero warnings, zero errors |
| `npx tsc --noEmit` | PASS — zero errors |
| `npm run build` | PASS — compiled in 1452ms, 2 static routes (/, /_not-found) |

---

## 2. Inventory

| File / Dir | Lines | Purpose |
|---|---|---|
| `app/favicon.ico` | — | Default Next.js favicon (unchanged from template) |
| `app/globals.css` | 8 | CSS entry: `@import "tailwindcss"` (v4) + `@layer base` body styles |
| `app/layout.tsx` | 22 | Root Server Component; sets `<Metadata>`, `lang="en"`, wraps `<Providers>` |
| `app/page.tsx` | 3 | Homepage: single `<h1 className="text-4xl font-bold text-blue-600">Carddroper</h1>` |
| `app/providers.tsx` | 31 | Client component; `QueryClient` (staleTime 30 s), `ReactQueryDevtools` in dev |
| `lib/api.ts` | 70 | `apiFetch<T>`, `ApiError` class, `ApiErrorBody` interface |
| `hooks/` | 0 | Empty directory (placeholder) |
| `components/` | 0 | Empty directory (placeholder) |
| `public/next.svg` | — | One remaining SVG (Next.js logo); not imported anywhere in TSX |
| `next.config.ts` | 7 | `output: "standalone"` only |
| `tsconfig.json` | 34 | `strict: true`, `moduleResolution: bundler`, `@/*` alias |
| `eslint.config.mjs` | 18 | ESLint v9 flat config; `eslint-config-next` core-web-vitals + typescript |
| `postcss.config.mjs` | 7 | `@tailwindcss/postcss` plugin (v4 pattern) |
| `package.json` | 29 | Next 16, React 19, RQ v5, Tailwind v4, TS 5 |
| `Dockerfile` | 39 | Three-stage: deps → builder (bakes `NEXT_PUBLIC_API_BASE_URL`) → runner |
| `.dockerignore` | 5 | Excludes `node_modules`, `.next`, `.git`, `*.md`, `.env*` |
| `.env.example` | 1 | `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` |
| `CLAUDE.md` | 1 | Agent routing hint (`@AGENTS.md`) |

**Total meaningful source lines: 126 (4 TS/TSX files + globals.css + configs).**

---

## 3. Findings

### F-1: No 401-silent-refresh logic in `apiFetch` — verified email / reset pages will fail silently on session expiry

- **Severity:** high
- **File:** `frontend/lib/api.ts` (entire file)
- **What:** `apiFetch` calls `fetch()` and throws `ApiError` on any non-OK response, with no retry on 401. The pattern source (`foodapp/frontend/src/lib/api.ts`) wraps every fetch call in a 401-intercept that: (a) skips refresh-exempt endpoints (`/auth/refresh`, `/auth/login`, `/auth/register`, etc.), (b) deduplicates concurrent 401 → refresh via a module-level `refreshPromise`, (c) retries the original request once after a successful token refresh, (d) tracks whether a session was ever established in `sessionStorage` (`HAS_SESSION_KEY`) to avoid spurious refresh attempts (e.g. on anonymous page load). None of this exists in Carddroper's `apiFetch`.
- **Why it matters:** Ticket 0014 will add `POST /auth/resend-verification` (requires an access token cookie). Access tokens live 15 minutes (`doc/systems/auth.md`). If a user lands on `/verify-email-sent`, waits (goes for coffee), then clicks "resend", the access token has expired. The page will throw an `ApiError(401)` with no chance of silently recovering. The user sees an error instead of a successful resend. This is the immediate failure mode; the same gap will bite every authenticated mutation the app ever adds.
- **Remediation:** Open a ticket (or fold into 0014) to add the deduplicating 401-refresh interceptor to `apiFetch` — exact shape in `foodapp/frontend/src/lib/api.ts:15–82`. Requires adding a `markLoggedOut()` / `clearLoggedOut()` session-signal pair so auth context can participate. Estimated size: ~80 lines (mostly in `lib/api.ts`); auth context integration is another ~20 lines.

---

### F-2: No auth context, no `useAuth` hook, no session-aware state anywhere

- **Severity:** high
- **File:** `frontend/` — repo-wide absence
- **What:** There is no `context/auth.tsx`, no `useAuth` hook, and no `GET /auth/me` query hook. `app/providers.tsx` only wraps `QueryClientProvider`. The canonical session source per `doc/architecture/overview.md` and conventions (`AGENTS.md`) is `GET /auth/me` via React Query with `staleTime: 30_000`. That query does not exist. The `verified_at` field — which ticket 0014's UI must expose (verification banner, gating paid actions) — has no surface in the frontend at all.
- **Why it matters:** Every page in 0014 (`/verify-email`, `/verify-email-sent`, `/login`, `/register`) needs `{ user, isLoading, isAuthenticated, isVerified }`. Without an auth context these pages will either re-implement `GET /auth/me` ad-hoc (anti-pattern that will diverge) or have no concept of who the user is. The `require_verified` pattern the backend enforces (403 on paid actions) has no complementary frontend gate.
- **Remediation:** 0014 must create `context/auth.tsx` with `AuthProvider` wrapping `useQuery({ queryKey: ['auth', 'me'], queryFn: () => apiFetch('/auth/me'), staleTime: 30_000 })`. Hook exposes `{ user, isLoading, isAuthenticated, isVerified }`. `app/providers.tsx` must nest `AuthProvider` inside `QueryClientProvider`. Estimated size: ~60 lines; required before any 0014 page is usable. Already called out in 0009 F-5 disposition ("planned for 0014").

---

### F-3: `apiFetch` does not wrap network-layer errors — `TypeError` will leak through React Query to UI

- **Severity:** medium
- **File:** `frontend/lib/api.ts:44`
- **What:** The `fetch()` call at line 44 has no `try/catch` around the network invocation. A connection refused (backend not running), DNS failure, or CORS preflight rejection throws a native `TypeError("Failed to fetch")` that is not an `ApiError`. Any component that writes `catch (err) { if (err instanceof ApiError) { ... } }` will silently fall through to an unhandled state. Already noted as 0009 F-8 with disposition "fold into 0014".
- **Why it matters:** 0014 will add the first mutation forms (register, login, verify-email). During development and in brief network outages, every form's error handler will silently swallow the error or display nothing. The user sees a frozen form with no feedback.
- **Remediation:** Wrap `fetch()` in try/catch; re-throw as `ApiError` with `status: 0` and `code: "NETWORK_ERROR"`. Confirmed fold into 0014 per 0009 disposition. Estimated size: ~8 lines in `lib/api.ts`.

---

### F-4: `QueryClient` missing `retry: false` (or a low count) for mutation/auth flows

- **Severity:** medium
- **File:** `frontend/app/providers.tsx:8-17`
- **What:** `makeQueryClient` sets `staleTime: 30_000` but leaves `retry` at the React Query v5 default (3 attempts with exponential backoff) and `refetchOnWindowFocus` at default (`true`). Already noted as 0009 F-4 with disposition "fold into 0014".
- **Why it matters:** When `GET /auth/me` is added as the session query, a 401 (logged-out state) will trigger 3 retry attempts with increasing delays before settling. This wastes ~15 seconds and generates 3 backend requests on every page load for a logged-out user. Additionally, `refetchOnWindowFocus: true` means every tab-switch triggers a `/auth/me` re-fetch (mitigated by `staleTime: 30_000` on fresh data, but on a stale session it will still fire). For mutation operations (register, verify, resend), the `retry` default is also 3 which is wrong for non-idempotent calls — though mutations individually can override this, a global default of `retry: 0` for mutations is safer.
- **Remediation:** In `providers.tsx`, set `defaultOptions.queries.retry: 1` (or `false`), `defaultOptions.queries.refetchOnWindowFocus: false`, and `defaultOptions.mutations.retry: 0`. Confirmed fold into 0014. Estimated size: 3 lines in `providers.tsx`.

---

### F-5: One leftover template SVG in `public/` — `next.svg` not deleted by ticket 0012

- **Severity:** low
- **File:** `frontend/public/next.svg`
- **What:** Ticket 0012 deleted `file.svg`, `globe.svg`, `vercel.svg`, and `window.svg` from `public/`. One template SVG remains: `next.svg` (Next.js logo). It is not imported anywhere in `app/**` or `components/**`. It was not in the 0012 acceptance list — the ticket brief explicitly named four files, and `next.svg` was not among them. It is not Vercel-branded but is dead dead code.
- **Why it matters:** Minor. Bloats the public directory with a framework logo. Not a security or functionality issue.
- **Remediation:** Delete as a drive-by on the first ticket that touches `public/`. No new ticket warranted. Estimated size: 1 file deletion.

---

### F-6: `undefined as T` type cast in `apiFetch` for 204 responses is technically unsound

- **Severity:** low
- **File:** `frontend/lib/api.ts:66`
- **What:** For 204 No Content responses, `apiFetch` returns `undefined as T`. This is a type lie — the caller declares `Promise<SomeType>` but receives `undefined`. In practice callers of 204 endpoints (e.g. logout) would correctly declare `Promise<void>` or `Promise<undefined>`, making the cast harmless. But nothing enforces that. A caller that writes `const user = await apiFetch<User>('/auth/logout')` will compile and get `undefined` at runtime.
- **Why it matters:** Low risk today with a tiny surface. As API client wrappers are added, a typed wrapper function (e.g. `api.delete<void>(...)`) is the right fix — the cast then only lives inside a single typed helper and callers never see it.
- **Remediation:** When wrapping `apiFetch` into `api.get / api.post / api.delete` convenience methods in 0014 (recommended pattern from foodapp), the 204 path can be typed to `Promise<undefined>` explicitly. The raw cast can stay for now. Estimated size: zero additional lines if handled during the wrapper pattern.

---

### F-7: No middleware.ts — no route protection, no auth-aware redirect at the edge

- **Severity:** medium (confirmed carry-over from 0009 F-5, disposition: "planned for 0014")
- **File:** `frontend/` — absent
- **What:** There is no `middleware.ts` and no route groups (`app/(auth)/`, `app/(dashboard)/`). Every future page is publicly accessible by default. The App Router's `middleware.ts` (Next.js edge middleware) is the correct chokepoint to check session-cookie presence and redirect before the page even renders. Per-layout server component guards are a fallback, not a primary defence.
- **Why it matters:** Without route groups, 0014 will need to invent the directory structure that all future pages will live under. Getting this wrong (e.g. placing auth pages at `app/login/` instead of `app/(auth)/login/`) will require directory renames later. Middleware also intercepts the verify-email flow correctly — if a fully authenticated and verified user lands on `/verify-email-sent`, middleware can redirect them to `/dashboard` rather than letting the page render a confusing "verify your email" state.
- **Remediation:** 0014 must establish: `app/(auth)/` route group (unauthenticated-only; redirects logged-in users), `app/(dashboard)/` group (authenticated-only; redirects to login), and `middleware.ts` checking cookie presence for dashboard routes. The cookie is HttpOnly so middleware can only check presence (not decode), which is sufficient for redirect logic — actual validity is confirmed by `GET /auth/me` in the AuthProvider. Estimated size: ~50 lines total (middleware + two layout files).

---

### F-8: `NEXT_PUBLIC_API_BASE_URL` absent at build time silently falls back to `localhost:8000` in the production bundle

- **Severity:** medium
- **File:** `frontend/lib/api.ts:1`, `frontend/Dockerfile:15-16`
- **What:** `lib/api.ts` line 1 uses `process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"`. The Dockerfile `builder` stage declares `ARG NEXT_PUBLIC_API_BASE_URL` and `ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}`, but if Cloud Build does not pass `--build-arg NEXT_PUBLIC_API_BASE_URL=https://api.staging.carddroper.com`, the ARG is empty, the ENV is set to an empty string `""`, and the fallback `??` does NOT trigger because `""` is not `undefined` or `null` in JavaScript — `process.env.NEXT_PUBLIC_API_BASE_URL` evaluates to `""` at runtime, so `"" ?? "http://localhost:8000"` returns `""`. Every API call becomes a relative-path fetch `""${path}` which resolves to the frontend origin (port 3000), not the backend (port 8000). **All API calls silently fail with 404 or wrong responses.**
- **Why it matters:** If Cloud Build's `--build-arg` is ever omitted (typo in `cloudbuild.yaml`, new environment spin-up, local Docker test without the arg), the production image is completely broken and the failure is not obvious — the build succeeds, the container starts, `/health` on the frontend serves HTML, but every authenticated or data-fetching call returns nothing useful.
- **Remediation:** Change `lib/api.ts` line 1 to use a non-empty guard: `const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";`. The `||` operator treats empty string as falsy and falls back correctly, matching foodapp's `api.ts:1` pattern exactly. Alternatively, add a build-time assertion. Estimated size: 1 character change (`??` → `||`).

---

### F-9: `.dockerignore` excludes `*.md` — `README.md` cannot be added without adjusting it

- **Severity:** nit
- **File:** `frontend/.dockerignore:4`
- **What:** `.dockerignore` contains `*.md`. This is fine for the current tree (no markdown except `CLAUDE.md`). If a `README.md` or changelog were added to the frontend root and referenced in the build (unlikely for Next.js), it would be silently excluded.
- **Why it matters:** Trivial. The `*.md` exclusion is correct and intentional. Only worth noting because `CLAUDE.md` is also excluded (not needed in the image — correct).
- **Remediation:** Nothing needed. Document as known if a future `README.md` appears and seems missing from the image.

---

## 4. Pre-0014 Readiness

### Verdict: **Conditional green-light**

0014 can proceed but must execute these items as part of its scope (not pre-requisites):

| Item | Where |
|---|---|
| Add 401-silent-refresh interceptor (F-1) | `lib/api.ts` |
| Create `context/auth.tsx` + `useAuth` hook (F-2) | `context/auth.tsx`, `app/providers.tsx` |
| Wrap `fetch()` in network-error catch (F-3) | `lib/api.ts` |
| Set `retry: 1`, `refetchOnWindowFocus: false`, `mutations.retry: 0` (F-4) | `app/providers.tsx` |
| Establish route groups `(auth)/` and `(dashboard)/` + `middleware.ts` (F-7) | `app/` directory structure |
| Fix empty-string env fallback (F-8) | `lib/api.ts` line 1 — 1-char fix |

None of the above are external dependencies that another team or ticket must resolve first. All are in `frontend/` and within 0014's scope. The scaffold is clean (lint green, types clean, build clean), so there is no debt to pay before writing new code.

**F-5** (stale `next.svg`) and **F-6** (204 cast) are non-blockers — clean up as drive-bys.

---

## 5. Open Items Deferred

The following 0009 deferred items remain open (no action taken since 0009):

| Finding | Status |
|---|---|
| 0009 F-2: `viewport` export on layout | Still deferred — no viewport-specific fields added since 0009; no warning emitted. Remains a "next layout touch" item. |
| 0009 F-6: `noUncheckedIndexedAccess` / `noImplicitOverride` in tsconfig | Still deferred — no action taken, not a blocker. |

These are not re-raised as new findings. Status is unchanged from 0009 disposition.
