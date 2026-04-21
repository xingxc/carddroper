# Frontend Audit — 2026-04-20

**Auditor:** frontend-builder agent
**Ticket:** 0009
**Scope:** `/Users/johnxing/mini/postapp/frontend/**` — read-only inventory + findings

---

## 1. Inventory

| File | Purpose |
|---|---|
| `app/favicon.ico` | Default Next.js favicon (unchanged from template) |
| `app/globals.css` | Global CSS entry point; imports Tailwind v4 and sets base body styles |
| `app/layout.tsx` | Root Server Component layout; sets `<Metadata>`, wraps in `<Providers>` |
| `app/page.tsx` | Home page — single `<h1>Carddroper</h1>` with Tailwind classes |
| `app/providers.tsx` | Client component; creates `QueryClient` (staleTime 30 s) and renders `ReactQueryDevtools` in dev |
| `lib/api.ts` | Core fetch helper: `apiFetch<T>`, `ApiError` class, `ApiErrorBody` interface |
| `next.config.ts` | Next.js config; sets `output: "standalone"` |
| `tsconfig.json` | TypeScript config; `strict: true`, `moduleResolution: bundler` |
| `eslint.config.mjs` | ESLint flat config; extends `eslint-config-next` core-web-vitals + typescript presets |
| `postcss.config.mjs` | PostCSS config; wires `@tailwindcss/postcss` plugin (Tailwind v4 pattern) |
| `package.json` | Dependencies: Next 16, React 19, TanStack Query v5, Tailwind v4, TypeScript 5 |
| `package-lock.json` | Lockfile committed — npm |
| `Dockerfile` | Three-stage build: `deps` → `builder` → `runner`; non-root `node` user; standalone output |
| `.env.example` | Single public var: `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` |
| `.dockerignore` | Excludes `node_modules`, `.next`, `.git`, `*.md`, `.env*` |
| `.gitignore` | Standard Next.js gitignore; excludes `*.tsbuildinfo`, `next-env.d.ts` |
| `CLAUDE.md` | Agent routing hint (`@AGENTS.md`) |
| `public/file.svg` | Default Next.js template SVG (unused) |
| `public/globe.svg` | Default Next.js template SVG (unused) |
| `public/vercel.svg` | Default Next.js template SVG / Vercel branding (unused) |
| `public/window.svg` | Default Next.js template SVG (unused) |

**Not present (expected for future tickets):** `middleware.ts`, `context/auth.tsx`, `hooks/`, `components/`, `app/(auth)/`, `app/(dashboard)/`.

---

## 2. Observable Checks

Bash execution was denied by the environment during this audit run. Commands and their expected invocation are listed; actual output could not be captured.

| Command | Status | Notes |
|---|---|---|
| `npm ci` | NOT RUN — Bash denied | Lockfile is committed; `node_modules/` was present on disk from prior install |
| `npm run build` | NOT RUN — Bash denied | — |
| `npm run lint` | NOT RUN — Bash denied | — |
| `npx tsc --noEmit` | NOT RUN — Bash denied | — |

Static observations substituting for tool output:

- `package-lock.json` is committed alongside `package.json` — lockfile hygiene satisfied.
- `tsconfig.json` contains `"strict": true` and `"noEmit": true` — type-check config correct.
- No `any` keyword found anywhere in `**/*.ts` / `**/*.tsx` via Grep.
- `eslint.config.mjs` loads `eslint-config-next` in flat-config format — correct for ESLint v9.
- `next.config.ts` sets `output: "standalone"` which is required for the Dockerfile's runner stage.

**Recommendation:** orchestrator should run the four commands manually (or CI output) before closing 0009, since the automated checks are the primary verification gate.

---

## 3. Findings

### F-1: Observable checks could not be run — audit partially incomplete
- **Severity:** medium
- **Category:** inconsistency
- **Location:** repo-wide (audit process)
- **What:** The Bash tool was denied during this audit session, so `npm ci`, `npm run build`, `npm run lint`, and `npx tsc --noEmit` were not executed. Build errors, lint warnings, and TypeScript errors (if any) are therefore unconfirmed.
- **Why it matters:** The ticket's verification gate explicitly requires actual command output counts. A finding or breakage that only appears during build (e.g., a missing `next-env.d.ts` import, a type error introduced by a Tailwind plugin's generated types) would be invisible to static file reading.
- **Proposed follow-up:** Orchestrator runs the four commands before closing 0009. If all pass, no new ticket needed. If any fail, scope into the appropriate follow-up ticket.

---

### F-2: No `viewport` metadata export — Next.js 15 deprecation warning
- **Severity:** low
- **Category:** doc-drift
- **Location:** `frontend/app/layout.tsx`
- **What:** `layout.tsx` exports a `metadata` object with `title` and `description` but no separate `viewport` export. Next.js 15 moved viewport configuration (themeColor, viewport width) out of `Metadata` into a dedicated `export const viewport: Viewport` export. The current scaffold omits it entirely; the default browser viewport is used.
- **Why it matters:** Next.js 15 emits a deprecation warning at build time if viewport-related fields appear inside `metadata`. Currently they don't appear at all (so no warning), but the first developer who adds `themeColor` or `viewport` to `metadata` will trigger the warning. No production breakage today, but it's a gap before v0.1.0 polish.
- **Proposed follow-up:** Fold into a general "layout hardening" pass before public launch. New ticket not needed now.

---

### F-3: Default Next.js template SVGs left in `public/`
- **Severity:** nit
- **Category:** dead-code
- **Location:** `frontend/public/file.svg`, `public/globe.svg`, `public/vercel.svg`, `public/window.svg`
- **What:** Four SVG assets from the `create-next-app` template remain in `public/`. None are referenced in any TSX file. `vercel.svg` specifically includes Vercel branding.
- **Why it matters:** Vercel branding shipped in a production bundle is slightly awkward; the others just bloat the static directory. No functional harm.
- **Proposed follow-up:** Delete in a housekeeping pass, or fold into the first ticket that adds real brand assets (logo, icons).

---

### F-4: `QueryClient` missing `retry` and `refetchOnWindowFocus` defaults
- **Severity:** low
- **Category:** design-smell
- **Location:** `frontend/app/providers.tsx:8-17`
- **What:** `makeQueryClient` sets `staleTime: 30_000` (correct) but does not configure `retry` (defaults to 3 in React Query v5) or `refetchOnWindowFocus` (defaults to `true`). For auth queries (`GET /auth/me`), three automatic retries on a 401 will spam the backend on logout/session-expiry. `refetchOnWindowFocus: true` will re-fire the `/auth/me` query every time the user tabs back — harmless with a 30 s stale time, but can still cause noisy network tabs.
- **Why it matters:** When the auth query (`GET /auth/me`) is added in ticket 0011, retrying a 401 three times is wasteful and may mask session-expiry UX. The pattern from the foodapp reference sets `retry: false` for auth queries specifically (or globally 0/1).
- **Proposed follow-up:** Fold into ticket 0011 when the auth query hook is added. One-line fix to `providers.tsx`.

---

### F-5: No auth context, protected-route mechanism, or middleware
- **Severity:** medium
- **Category:** missing (expected scaffold gap)
- **Location:** `frontend/` — repo-wide absence
- **What:** There is no `context/auth.tsx`, no `middleware.ts`, and no route groups (`app/(auth)/`, `app/(dashboard)/`) that would enforce authentication. The current surface is a single public page, so this is expected for the scaffold phase, but ticket 0011 will need to add all of these.
- **Why it matters:** Any feature page added before auth guards are in place is publicly accessible by default. The Next.js middleware approach (check cookie presence → redirect) is the correct chokepoint for App Router; per-layout checks are a fallback, not a substitute. Getting the routing shape wrong now will require renaming app directories.
- **Proposed follow-up:** See Ticket 0011 callout below. This is not a bug today; it is pre-work that 0011 must include in its scope.

---

### F-6: `noUncheckedIndexedAccess` and `noImplicitOverride` not enabled
- **Severity:** nit
- **Category:** inconsistency
- **Location:** `frontend/tsconfig.json`
- **What:** `strict: true` is set, which enables the standard strict suite (`strictNullChecks`, `strictFunctionTypes`, etc.). However, `noUncheckedIndexedAccess` and `noImplicitOverride` are not present. The tech-stack doc does not mandate them, so this is not a violation, but they catch real bugs (array out-of-bounds, accidental override).
- **Why it matters:** Low risk at scaffold size; more relevant once hooks and components multiply. Not worth a ticket.
- **Proposed follow-up:** Nothing needed. Document as known gap if team style guide is ever written.

---

### F-7: Dockerfile `runner` stage does not set `HOSTNAME`
- **Severity:** low
- **Category:** design-smell
- **Location:** `frontend/Dockerfile` (runner stage)
- **What:** The runner stage sets `NODE_ENV=production` and switches to `USER node`, but does not set `ENV HOSTNAME=0.0.0.0`. The Next.js standalone server binds to `0.0.0.0` by default when run via `node server.js`, so in practice this works. However, the official Next.js standalone deployment docs recommend explicitly setting `HOSTNAME` to avoid surprises if the default changes.
- **Why it matters:** Not a current breakage; cosmetic guard against future Next.js behaviour change.
- **Proposed follow-up:** Fold into any Dockerfile-touching ticket. One-line addition: `ENV HOSTNAME=0.0.0.0`.

---

### F-8: `apiFetch` does not handle network errors distinctly from API errors
- **Severity:** low
- **Category:** design-smell
- **Location:** `frontend/lib/api.ts:44-48`
- **What:** `apiFetch` calls `fetch()` without a try/catch around the network layer. A DNS failure, connection refused, or CORS preflight rejection will throw a native `TypeError` (e.g., "Failed to fetch") that propagates untyped through React Query's error boundary. Component-level error handlers that check `error instanceof ApiError` will miss these cases and either crash or display unhelpful fallback text.
- **Why it matters:** During development (backend not running) and in brief network outages, the UX will show a raw `TypeError` rather than a friendly "Cannot reach server" message. This becomes more important once login / payment forms are added in tickets 0011-0013.
- **Proposed follow-up:** New ticket or fold into 0011 (first form that calls `apiFetch`). Wrap the `fetch()` call in try/catch; re-throw a typed `NetworkError` or an `ApiError` with `status: 0` and `code: "NETWORK_ERROR"`.

---

## 4. Callouts for Upcoming Tickets

### Ticket 0011 (email verification frontend)

Current auth-flow pages and routing shape:

**Pages that exist today:**

| Route | File | Type | Notes |
|---|---|---|---|
| `/` | `app/page.tsx` | Server Component | Scaffold only — `<h1>Carddroper</h1>` |

**Pages that do NOT exist yet (needed for 0011):**

| Intended route | Suggested file path | Purpose |
|---|---|---|
| `/login` | `app/(auth)/login/page.tsx` | Login form |
| `/register` | `app/(auth)/register/page.tsx` | Registration form |
| `/verify-email` | `app/(auth)/verify-email/page.tsx` | Landing page for email verification link (`?token=...` query param) |
| `/forgot-password` | `app/(auth)/forgot-password/page.tsx` | Request password-reset email |
| `/reset-password` | `app/(auth)/reset-password/page.tsx` | Password reset form (`?token=...` query param) |

**Route group convention to establish in 0011:**

- `app/(auth)/` — unauthenticated-only pages; layout redirects logged-in users to `/dashboard` (or equivalent).
- `app/(dashboard)/` — authenticated pages; layout or middleware redirects unauthenticated users to `/login`.

**Auth state plumbing needed before 0011 can work:**

- `context/auth.tsx` — `AuthProvider` wrapping `GET /auth/me` via `useQuery`; `useAuth()` hook returning `{ user, isLoading, isAuthenticated }`. Does not exist yet.
- `app/providers.tsx` — `AuthProvider` must be added inside `QueryClientProvider` (auth query depends on the QueryClient being available).
- `middleware.ts` — Next.js edge middleware for route protection; checks session cookie presence and redirects. Does not exist yet.

**Backend endpoints required by 0011 (verify against `doc/reference/backend-api.md` before briefing):**

- `GET /auth/me` — current user session
- `POST /auth/register` — registration
- `POST /auth/login` — login
- `POST /auth/logout` — logout
- `GET /auth/verify-email?token=...` — email verification token redemption

**Current `apiFetch` shape (what 0011 will call):**

```typescript
apiFetch<T>(path: string, init?: RequestInit): Promise<T>
// Throws ApiError on non-2xx; passes credentials: "include" always.
// No retry logic; no network-error wrapping (see F-8).
```

---

### Ticket 0012 (Stripe Customer on signup)

Frontend has no Stripe dependencies installed yet. The following are absent:

- `@stripe/stripe-js` — not in `package.json`
- `@stripe/react-stripe-js` — not in `package.json`
- `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` — not in `.env.example`

The registration page (`app/(auth)/register/page.tsx`) does not exist. Stripe Customer creation is a backend-side side-effect on `POST /auth/register`; the frontend impact of ticket 0012 is primarily the addition of the `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` env var and confirming the registration form submission returns the correct user shape (including `stripe_customer_id` if the frontend needs to surface it).

---

### Ticket 0013 (Stripe webhook / payments)

No payment UI exists. When this ticket arrives, `@stripe/react-stripe-js` and `@stripe/stripe-js` will need to be added as dependencies. The `apiFetch` helper deliberately does not handle card data (correct — card data goes directly to Stripe Elements, never through `lib/api.ts`). No additional callout needed at this time.
