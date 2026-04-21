---
id: 0009
title: scaffold code audit — backend + frontend ground-truth inventory before v0.1.0 features
status: resolved
priority: high
found_by: orchestrator 2026-04-20
resolved_by: orchestrator 2026-04-20
---

## Resolution

Both agents completed their audits. Reports:
- `doc/audits/2026-04-20-backend-audit.md` — 10 findings.
- `doc/audits/2026-04-20-frontend-audit.md` — 8 findings.

**Totals across both reports:** 0 blocker, 1 high, 6 medium, 6 low, 5 nit.

**Observable-checks gap closed:** frontend F-1 flagged that the agent could not run `npm ci / build / lint / tsc` (Bash denial). Orchestrator ran them manually after the report landed:

| Command | Result |
|---|---|
| `npm ci` | 147 pkgs installed, 0 vulnerabilities |
| `npm run build` | ✓ compiled in 2.2s, TS 1234ms, static routes /, /_not-found |
| `npm run lint` | 0 issues |
| `npx tsc --noEmit` | 0 errors |

Backend observable-checks: pytest 11/11 passed, alembic single-head confirmed from filesystem, `.env.example` mirrors all Settings fields.

### Dispositions

#### Backend findings

| # | Severity | Disposition |
|---|---|---|
| F-1 | high | **New ticket 0011** — backend error handling hardening (global 500 handler). |
| F-2 | medium | **Folded into 0010** — "no API key" fallback must not log `body_text` or token URLs; only `template` + `to_hash`. |
| F-3 | medium | **New ticket 0011** — paired with F-1: add `iss="carddroper"` + `aud="carddroper-api"` to every token payload, validate both on decode. |
| F-4 | medium | **Folded into 0010** — delete `backend/requirements.txt`, switch Dockerfile to `pip install .` from `pyproject.toml`. |
| F-5 | medium | **New ticket 0012** — Dockerfile hardening (non-root, multi-stage, HEALTHCHECK). Before prod deploy. |
| F-6 | low | **Deferred backlog** — add `asyncio_default_fixture_loop_scope = "function"` in next maintenance pass. |
| F-7 | low | **Deferred backlog** — explicit `BCRYPT_ROUNDS` setting. |
| F-8 | low | **Documented in 0015 draft** (Stripe Customer on signup) — the `stripe` dep stays; first import starts there. |
| F-9 | low | **Deferred backlog** — add `pytest-cov` + coverage gate. |
| F-10 | nit | No action. |

#### Frontend findings

| # | Severity | Disposition |
|---|---|---|
| F-1 | medium | **Closed** — orchestrator ran the four commands manually, all green (see table above). |
| F-2 | low | **Deferred backlog** — `viewport` export on next layout touch. |
| F-3 | nit | **Folded into 0012** — delete unused template SVGs during Dockerfile hardening pass. |
| F-4 | low | **Folded into 0014** (email verification frontend) — set `retry: false` on auth queries when wiring `useAuth`. |
| F-5 | medium | **Planned for 0014** — expected scaffold gap; 0014 adds `context/auth.tsx`, `middleware.ts`, route groups. |
| F-6 | nit | No action. |
| F-7 | low | **Folded into 0012** — set `ENV HOSTNAME=0.0.0.0` on frontend runner stage. |
| F-8 | low | **Folded into 0014** — wrap `apiFetch` fetch call in try/catch, emit typed `NetworkError` / `ApiError` with `status: 0`. |

### Follow-up tickets created

- **0011** — backend error handling + JWT claims hardening (F-1 + F-3 backend).
- **0012** — Dockerfile + public/ hardening (F-5 backend, F-3 F-7 frontend).

Ticket 0010 (SendGrid) pre-requisite is satisfied by this audit. 0010 has been updated with concrete callsite line numbers and the F-2 / F-4 fixes baked into its Phase 0 brief.

### Deferred backlog (no ticket, tracked here)

- Backend F-6: pytest-asyncio loop scope declaration.
- Backend F-7: `BCRYPT_ROUNDS` as explicit Settings field.
- Backend F-9: `pytest-cov` + coverage gate.
- Frontend F-2: `viewport` export.
- Frontend F-6: `noUncheckedIndexedAccess`, `noImplicitOverride` in tsconfig.

Pick these up in a housekeeping ticket at the end of v0.1.0 push, or as drive-bys when touching the relevant files.

---

## Context

Phase 1 of PLAN.md §10 (backend scaffold + auth) and §10.2 (frontend scaffold) are marked done. Before we layer email / Stripe / verification on top, we need a **written ground-truth audit** of what actually exists in `backend/app/**` and `frontend/**` — file-by-file inventory + findings categorized by severity — so every downstream ticket briefs against reality instead of against the PLAN docs.

Why now, not inline with feature tickets:

- **Orchestrator blind spot.** The orchestrator (per CLAUDE.md) does not read `app/**` or `frontend/app/**`. Every feature brief I write has to assume what's there, and scaffolds never perfectly match their planning docs. A written inventory collapses that guessing game for every subsequent ticket.
- **Latent bugs compound.** A sync `requests` call on an async handler is invisible under single-user testing but blocks the event loop under real traffic. Same pattern applies to missing indexes, unvalidated CORS, overly permissive cookie flags. Easier to fix one at a time now than after they're wrapped in features.
- **Ticket 0010 depends on it.** The SendGrid hardening ticket needs the exact list of `email_service` callsites to reshape safely — trying to discover them mid-build means mid-build scope creep.

**This ticket is READ-ONLY.** Agents produce audit reports. Agents do not fix, refactor, or add tests. Findings are triaged by the orchestrator into follow-up tickets after both reports land.

## Pre-requisites

- Ticket 0008 resolved (staging live — confirms scaffold runs end-to-end at least in one environment).
- Agents can run the app locally (`docker compose up` or direct backend/frontend dev loop). If they cannot, that itself is finding #1 and the report should capture it.

## Acceptance

### Phase 0 (parallel): backend-builder + frontend-builder audit reports

Dispatch **backend-builder** and **frontend-builder** in parallel. Each writes a dated markdown report:

- `doc/audits/2026-04-20-backend-audit.md` (backend-builder)
- `doc/audits/2026-04-20-frontend-audit.md` (frontend-builder)

**Both reports must have these sections, in order:**

1. **Inventory** — file tree of their domain, one line per file: path + one-sentence purpose. Skip `__pycache__`, `node_modules`, `.next`, generated artifacts.
2. **Observable checks** — commands they ran + result, one line each. E.g. `pytest → 47 passed, 0 failed`, `ruff check → 3 warnings (listed below)`, `npm run typecheck → 0 errors`.
3. **Findings** — each finding gets a severity, category, location, description, proposed follow-up. Template:

   ```
   ### F-N: <short title>
   - **Severity:** blocker | high | medium | low | nit
   - **Category:** bug | security | design-smell | missing-test | inconsistency | dead-code | dep-hygiene | doc-drift
   - **Location:** backend/app/routes/auth.py:123  (or "repo-wide")
   - **What:** one paragraph — observed behaviour / code shape.
   - **Why it matters:** one paragraph — concrete failure mode, not "this is ugly".
   - **Proposed follow-up:** new ticket | fold into 0010 | fold into 0011 | defer | nothing needed.
   ```

   Severity rubric (use consistently):
   - **blocker** — wrong output, security hole, or crash under normal use.
   - **high** — latent bug that bites under load / concurrency / retry, OR missing security control that PLAN.md says we have.
   - **medium** — design smell that will cost us in the next feature ticket if not fixed.
   - **low** — cleanup worth doing but not time-sensitive.
   - **nit** — taste, style; probably don't file a ticket.

4. **Callouts for upcoming tickets** — explicit list with this shape:

   ```
   - Ticket 0010 (SendGrid hardening) needs:
       - current email_service.py public API: <signature>
       - all callsites: backend/app/routes/auth.py:L42, L87, L134
       - any helper wrappers (send_verification_email, send_reset_email, etc.): path:line
   - Ticket 0011 (email verification polish) needs:
       - current register flow shape (which status it returns when send fails, whether email is best-effort or blocking)
       - current verify-token storage: table/column names, expiry field, reuse prevention
   - Ticket 0012 (Stripe Customer on signup) needs:
       - exact register-flow insertion point
       - users.stripe_customer_id current nullability + index
   ```

### Backend audit — specific scope

Orchestrator dispatches **backend-builder** with this brief:

```
Task: Read-only audit of backend/ for ground-truth inventory + findings report.
Output: a single markdown file at doc/audits/2026-04-20-backend-audit.md following
  the four-section template in ticket 0009.

READ-ONLY: do not Edit, Write, or delete any source file. The only file you create
  is the audit report itself.

Cover these dimensions (not exhaustive — add findings outside this list freely):

  1. Async correctness
     - Every `async def` endpoint: does it call sync libraries directly?
       (requests, sync SDK methods, file I/O without asyncio.to_thread)
     - Every SQLAlchemy session: AsyncSession vs Session; are they used consistently?
     - Any blocking sleeps, loops, CPU work on the event loop.

  2. Auth + security
     - Password hashing: bcrypt direct call, rounds count, comparison constant-time.
     - JWT: algorithm (HS256), exp claim timezone (UTC aware?), iss/aud claims
       present, signature verification on every protected route.
     - Refresh tokens: stored as SHA-256 hash, never raw; rotation on use; expiry.
     - Rate limits: slowapi wired? which routes have it? matching .env.example strings?
     - CORS: allowed origins list vs wildcard.
     - Cookie flags: Secure, HttpOnly, SameSite. Does COOKIE_SECURE respect env var?
     - Login lockout: per-account counter, window, duration — wired correctly?
     - HIBP check on register / password change?
     - Any secrets in logs (stack traces leaking tokens, DB URLs with creds, etc.)?

  3. Database layer
     - Migration chain integrity: alembic history → single linear chain, no branches?
     - Model → migration consistency: all model columns present in initial migration?
       (Spot-check 2-3; flag divergence.)
     - Required indexes present? users.email unique? refresh_tokens.user_id?
       login_attempts.user_id + timestamp for lockout queries?
     - Default values for NULL columns: pragmatic or carrying legacy?
     - Foreign key ON DELETE behavior (CASCADE vs RESTRICT vs SET NULL).
     - UTC vs naive datetimes in column types.
     - email_verifications / password_reset tokens: storage shape, expiry, single-use?

  4. Errors + logging
     - Are HTTPException used consistently vs custom errors?
     - Is there a global exception handler mapping unknown errors to 5xx JSON?
     - Does the logger emit JSON in prod? Is there a request_id correlation field?
     - PII in logs (email addresses, tokens, IPs)?

  5. Config / Settings
     - pydantic-settings BaseSettings: every field has a default? Which are required?
     - SecretStr used for keys? Or plain str?
     - .env.example covers every field the code reads?
     - Multiple Settings instances or a singleton?

  6. Tests
     - pytest runs clean? Count.
     - Coverage: is it configured? % if reported. Which files have zero coverage?
     - Are auth routes tested for both happy path and rate-limit / lockout / expired-token?
     - Do any tests actually hit a DB (sqlite-memory, real Postgres, mock)?

  7. Dep hygiene
     - pyproject.toml: pinned versions? any long-unmaintained packages?
     - Duplicate deps across sections?
     - Any dep imported in code but not in pyproject.toml?

  8. Dockerfile + runtime
     - Python version matches pyproject.toml requires-python?
     - Multi-stage or single-stage? Root user or non-root?
     - WORKDIR + entrypoint; signals / graceful shutdown.
     - Healthcheck directive?

  9. Dead code / TODOs / FIXMEs
     - Grep the tree for FIXME / TODO / XXX; list each with one-line context.
     - Any modules imported nowhere?
     - Any routes registered but not in doc/reference/backend-api.md?

  10. Dev ergonomics
     - docker-compose.yml: backend + db + frontend all start cleanly?
     - Hot reload wired for backend?
     - `make` or poetry shortcuts present?

Observable checks to actually run:
  - poetry run pytest   (report pass/fail counts + any slow markers)
  - poetry run ruff check app/ tests/   (if ruff is configured; else skip)
  - poetry run mypy app/   (if mypy is configured; else skip)
  - poetry run alembic heads   (must be single; multiple = branch = bug)
  - poetry run alembic check  (optional; warns if models diverge from migrations)
  - docker compose config     (validates compose file)

Explicit Ticket 0010 callout required — the reshape brief depends on knowing:
  - current email_service.py public API (top-level defs with signatures)
  - every callsite (file:line, calling function, surrounding 2-line context)
  - any wrappers (e.g., send_verification_email() that in turn calls send_email())
  - current settings fields related to email

Explicit Ticket 0011-0013 callouts required (email verification, Stripe customer,
  Stripe webhook) for the fields listed in the Acceptance section #4 above.

Do NOT:
  - Edit any file under backend/.
  - Refactor, rename, delete anything.
  - Add tests or fixtures.
  - Install new packages.

Report format: follow the four-section template from doc/issues/0009-code-audit.md
  verbatim. Max total length ~2500 words — be terse, findings should fit in their
  template, not sprawl.
```

### Frontend audit — specific scope

Orchestrator dispatches **frontend-builder** with this brief:

```
Task: Read-only audit of frontend/ for ground-truth inventory + findings report.
Output: a single markdown file at doc/audits/2026-04-20-frontend-audit.md following
  the four-section template in ticket 0009.

READ-ONLY: do not Edit, Write, or delete any source file. The only file you create
  is the audit report itself.

Cover these dimensions (not exhaustive):

  1. Next.js structure
     - App Router (app/) vs Pages Router (pages/); no mixing.
     - Server components vs client components: "use client" directives correct?
     - Metadata / viewport / icons configured?
     - Root layout shape (providers wrapping order: QueryClient, Theme, Auth, etc.).

  2. TypeScript strictness
     - tsconfig.json: strict: true? noUncheckedIndexedAccess? noImplicitOverride?
     - Any `any` in the codebase? Count them.
     - Module resolution (bundler, node, etc.)?

  3. Tailwind v4
     - @import "tailwindcss" in the entry CSS (v4 pattern, not v3 config.js).
     - Any v3-style tailwind.config.js leftover?
     - Dark mode configured?

  4. React Query v5
     - QueryClient configured (defaults: staleTime, retry, refetchOnWindowFocus)?
     - Devtools wired in dev only?

  5. API client (lib/api.ts)
     - Base URL resolution: env var + fallback?
     - Credentials handling (cookies or bearer token or both)?
     - Error shape: does the client unwrap / normalize errors before they hit components?
     - Retry / abort / interceptor logic?

  6. Auth state
     - Where is the current-user state? Context, Query, cookie-only?
     - Protected-route pattern: middleware, layout check, per-page guard?
     - Token refresh: who triggers it — interceptor, hook, route handler?

  7. Forms
     - Are there form components yet (register / login)? If yes, validation library?
       (react-hook-form + zod? native?)
     - Error messages wired to inputs?

  8. Accessibility basics
     - Semantic HTML on scaffold pages (heading hierarchy, label for inputs).
     - Focus styles not removed.
     - Images have alt text.

  9. Build + type-check + lint
     - npm run build — success? bundle size report?
     - npm run lint (eslint) — warnings/errors count.
     - tsc --noEmit — errors count.
     - Any build warnings about imports / missing env vars?

  10. Dep hygiene
     - package.json: any deprecated packages? duplicates?
     - Lockfile committed (package-lock.json or pnpm-lock or yarn.lock)?

  11. Dockerfile
     - Multi-stage (deps / builder / runner)? Non-root?
     - standalone output wired?
     - NEXT_PUBLIC_* ARGs / ENVs plumbed correctly?

  12. Dev ergonomics
     - `npm run dev` works? Hot reload? Port consistent with compose?

Observable checks to actually run:
  - npm ci        (confirm lockfile resolves cleanly)
  - npm run build (report success + bundle sizes)
  - npm run lint  (if eslint configured)
  - npx tsc --noEmit

Explicit Ticket 0011 callout required (email verification frontend): list the
  current auth-flow pages (register, login, verify-email landing, etc.) and their
  current routing shape so the verify-email landing page can be briefed against
  the actual router.

Do NOT:
  - Edit any file under frontend/.
  - Install new packages.
  - Add tests, components, or styles.

Report format: follow the four-section template from doc/issues/0009-code-audit.md
  verbatim. Max total length ~2000 words. Frontend surface today is minimal — the
  report will be correspondingly short and that's fine.
```

### Phase 1: orchestrator — triage and disposition

Once both reports land:

1. Orchestrator reads both audit files end-to-end.
2. For each **blocker / high** finding: decide disposition — new ticket | fold into 0010 | fold into an existing planned ticket | roll forward as known risk. Write the disposition next to the finding.
3. For each **medium** finding: same triage, weaker bar.
4. **Low / nit** findings: log once in the audit and forget unless they cluster.
5. Update 0010 (and any other drafted tickets) with the concrete call-site lines from the audit callouts.
6. Append a "Resolution" section to this ticket listing: total findings by severity, disposition breakdown, any tickets created as follow-ups.

## Verification

**Automated checks:**

```bash
test -f /Users/johnxing/mini/postapp/doc/audits/2026-04-20-backend-audit.md
test -f /Users/johnxing/mini/postapp/doc/audits/2026-04-20-frontend-audit.md
wc -w doc/audits/2026-04-20-*.md   # sanity: each under ~2500 words
```

**Functional smoke (orchestrator, not agent):**

- Both audit files open, render, contain all four required sections.
- Each "blocker" and "high" finding has a disposition appended by orchestrator.
- Ticket 0010 has been updated (or confirmed unchanged) based on the audit callouts before 0010 Phase 0 is dispatched.

## Out of scope

- **Fixing anything.** This is strictly read-only. If an agent is tempted to fix even a trivial thing, they must report it as a finding instead.
- Infra audit (cloudbuild.yaml, GCP config, DNS) — those have already been validated through the 0006-0008 chain and are re-audited implicitly every deploy.
- Doc review (spelling, freshness of `doc/systems/*.md`). Separate hygiene ticket if needed.
- Performance benchmarking. Premature without real features.
- Dependency CVE scanning (run manually later or wire a CI action).

## Report

Each agent reports:
- Path to its audit file.
- One-paragraph executive summary (headline: how many findings at each severity, and any single issue that overshadows the rest).
- Whether any blocker was found — if yes, short description inline in the chat reply (so orchestrator doesn't have to open the file to decide next step).

Orchestrator reports (in this ticket's Resolution section):
- Count of findings by severity across both reports.
- Disposition per blocker / high finding.
- Follow-up ticket numbers created.
- Any updates pushed to 0010 based on callouts.

## Resolution

*(filled in by orchestrator on close, after both audit files exist and triage is done)*
