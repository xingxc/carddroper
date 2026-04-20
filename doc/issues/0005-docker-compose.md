---
id: 0005
title: docker-compose — Postgres + backend + frontend, one command up
status: resolved
priority: high
found_by: orchestrator 2026-04-19
resolved_at: 2026-04-19
---

## Context

PLAN.md §10.3. Backend and frontend both run standalone now (tickets 0001–0004), but there's no single command that brings up the full stack with a real database. We need that before §10.4 (first staging push) because the same container that runs locally must be the one that ships to Cloud Run — surprises only show up when both sides are wired together.

Three workstreams: a frontend Dockerfile (frontend-builder), a backend Dockerfile review + `.env.example` regeneration (backend-builder), and the compose file itself (orchestrator). Each is dispatched separately; this ticket tracks all three to completion.

Reference: `doc/operations/development.md` already specifies the target shape (Postgres 5433, backend 8000, frontend 3000, DB name `carddroper`).

## Acceptance

### Part A — frontend Dockerfile (frontend-builder)

1. Create `/Users/johnxing/mini/postapp/frontend/Dockerfile`. Multi-stage:
   - **deps** stage: `node:20-alpine`, copy `package.json` + `package-lock.json`, `npm ci`.
   - **builder** stage: copy source, `npm run build`. `NEXT_PUBLIC_API_BASE_URL` must be a build arg so it gets baked into the client bundle correctly.
   - **runner** stage: `node:20-alpine`, copy `.next/standalone` + `.next/static` + `public`, `EXPOSE 3000`, `CMD ["node", "server.js"]`. Run as a non-root `node` user.
2. Add `output: "standalone"` to `next.config.ts` so the standalone build emits the trimmed runtime bundle.
3. Create `/Users/johnxing/mini/postapp/frontend/.dockerignore` — exclude `node_modules`, `.next`, `.git`, `*.md`, `.env*`.
4. Image must build clean: `docker build -t carddroper-frontend ./frontend` exits 0.

### Part B — backend Dockerfile + env review (backend-builder)

1. Verify `/Users/johnxing/mini/postapp/backend/Dockerfile` still builds clean after the ticket-0003 passlib removal. `docker build -t carddroper-backend ./backend` exits 0. If it doesn't, fix and report what was wrong.
2. Regenerate `/Users/johnxing/mini/postapp/backend/.env.example` (it was deleted at some point — see `git status`). Required keys: `DATABASE_URL`, `JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `CORS_ORIGINS`. Use placeholder values (`change-me`, `localhost` URLs). The `DATABASE_URL` placeholder should match the compose layout: `postgresql+asyncpg://carddroper:carddroper@db:5432/carddroper`.
3. Verify there's a `/health` route returning 200 with a JSON body. If not, add one (this is the smoke target compose's healthcheck and §10.4's first staging push will both hit).
4. Re-run `.venv/bin/pytest tests/` — confirm 10/10 still green.

### Part C — docker-compose.yml (orchestrator)

1. Create `/Users/johnxing/mini/postapp/docker-compose.yml` with three services:
   - **db**: `postgres:16-alpine`, volume-mounted data dir, `POSTGRES_USER/PASSWORD/DB=carddroper`, host port `5433` mapped to container `5432`, healthcheck via `pg_isready`.
   - **backend**: builds from `./backend`, `depends_on: db: condition: service_healthy`, env from `backend/.env`, `DATABASE_URL` overridden in compose to use the `db` service hostname, host port `8000`, healthcheck hitting `/health`.
   - **frontend**: builds from `./frontend` with build-arg `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` (browser hits localhost, not the compose-internal hostname), `depends_on: backend: condition: service_healthy`, host port `3000`.
2. Named volume for Postgres data so `docker-compose down` preserves DB; `docker-compose down -v` wipes it (matches `doc/operations/development.md`).
3. No production-only concerns (TLS, secrets manager) — this file is dev-only. Document at the top with a comment.

## Verification

**Automated checks (run after all three parts land):**
- `docker-compose build` — exits 0.
- `docker-compose up -d` — all three services start; `docker-compose ps` shows all healthy within 60s.
- `docker-compose exec backend alembic upgrade head` — exits 0.
- `docker-compose exec backend pytest tests/` — 10/10 green inside the container.
- `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health` — returns 200.
- `curl -s http://localhost:3000 | grep -o "Carddroper"` — finds the heading in SSR HTML.
- `docker-compose down` — exits cleanly.

**Functional smoke (orchestrator runs after agent parts land, surfaces results to user):**
- Open `http://localhost:3000` in a browser → "Carddroper" renders, no console errors, Tailwind styles applied.
- Open `http://localhost:8000/docs` → FastAPI Swagger UI renders, `/auth/*` endpoints listed.
- `psql postgresql://carddroper:carddroper@localhost:5433/carddroper -c "\dt"` → lists `users`, `refresh_tokens`, `email_verifications` tables (proves migrations ran against the compose DB, not just locally).

## Out of scope

- Frontend → backend auth wiring (separate ticket — auth pages first).
- Stripe integration (Phase 2).
- Email/SendGrid integration (separate ticket).
- Production-grade Dockerfile concerns (multi-arch builds, non-root in backend, distroless base, signing) — local dev uses what works; Cloud Run hardening lands in §10.4.
- SSR-side API calls from frontend (would require dual `INTERNAL_API_BASE_URL` env var; not needed yet since no server components fetch).
- CI integration (compose in GitHub Actions) — separate ticket if/when we add CI.

## Report

Each agent reports its part separately:

**frontend-builder (Part A):**
- Files created (`frontend/Dockerfile`, `frontend/.dockerignore`).
- `next.config.ts` change (one line, `output: "standalone"`).
- `docker build` result + image size.
- Any deviation from the spec.

**backend-builder (Part B):**
- Files touched (`backend/Dockerfile` if changed, `backend/.env.example` regenerated, `app/main.py` or wherever `/health` lives if added).
- Pytest result (X/10).
- `docker build` result.
- Any deviation.

**Orchestrator (Part C):**
- `docker-compose.yml` created.
- All automated checks above passing.
- Functional smoke surfaced to user with results.

## Resolution

`docker-compose.yml` written at repo root with three services: `db` (postgres:16-alpine, host port 5433, named volume `carddroper_pgdata`, pg_isready healthcheck), `backend` (built from `./backend`, depends on db healthy, env from `backend/.env` with `DATABASE_URL` overridden to compose hostname, `python -c urllib` healthcheck on `/health`), `frontend` (built from `./frontend` with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` build arg, depends on backend healthy). Bring-up: `docker-compose up -d`. All three healthy in <30s.

**Migration handling:** Ticket originally specified `docker-compose exec backend alembic upgrade head` as a separate post-up step. That created a chicken-and-egg failure — backend's startup hook prunes `refresh_tokens`, which doesn't exist until migrations run. Resolved by adding `command: ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]` to the backend service so `up` is genuinely one command. Production deploys still run migrations as a separate Cloud Build step. `doc/operations/development.md` updated to drop the now-obsolete exec step.

**Verification adjustment:** Ticket's "pytest 10/10 inside container" check was an overreach — the production-shaped backend image correctly omits dev deps (no pytest in PATH). Pytest already runs on the host via Part B (11/11 with the new `test_health`). Removing this from the verification standard for future container-based tickets.

**Smoke gap fixed:** Browser smoke surfaced that the homepage `<h1>` had no Tailwind utility classes, making "Tailwind compiled" visually indistinguishable from "Tailwind broken" (Tailwind v4 preflight resets browser defaults to plain text). One-line follow-up dispatch added `className="text-4xl font-bold text-blue-600"` to `app/page.tsx` for visual proof. User confirmed in browser.

**Final automated checks (all green):** docker-compose build exit 0; up brings all 3 services healthy; `/health` returns 200; `/docs` returns 200; SSR HTML contains "Carddroper"; tables `users`/`refresh_tokens`/`login_attempts`/`alembic_version` present in compose DB; backend pytest 11/11 (host); frontend tsc clean, lint clean, build clean.

**Functional smoke (user-verified):** http://localhost:3000 renders Carddroper as large bold blue heading (Tailwind end-to-end proven). http://localhost:8000/docs renders FastAPI Swagger UI with all `/auth/*` and `/health` endpoints listed.
