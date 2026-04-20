# Tech Stack

## Backend

**FastAPI (Python 3.11)** — async-native web framework. Chosen because it matches the foodapp pattern we're reusing, has excellent Pydantic validation, and auto-generates OpenAPI docs at `/docs` which shortens the feedback loop during development. Alternatives considered:
- Express/Node: would let us share TypeScript with the frontend. Rejected because Python's type system plus Pydantic is more robust for API contracts, and the existing foodapp auth code is in Python.
- Django: heavier, more opinionated. Overkill for an API-only backend.
- Go (Gin/Echo): faster runtime, but slower iteration and further from the foodapp reference.

**SQLAlchemy 2.0 (async) + asyncpg** — async ORM. Same stack as foodapp, battle-tested, handles connection pooling correctly. `expire_on_commit=False` so objects remain usable after commit.

**Alembic** — migrations. Fresh chain; we don't import foodapp's migration files. One migration per PR, named for the change.

**Pydantic v2 + pydantic-settings** — request/response validation and `.env` loading.

**slowapi** — per-IP rate limiting on auth mutations (register, login, refresh, logout, forgot-password, verify-email, resend-verification).

**bcrypt** — password hashing, used directly (no passlib wrapper). `bcrypt==4.0.1` pinned to avoid the 4.1.0 incompatibility that foodapp hit. passlib was removed in ticket 0003 to stay compatible with Python 3.13 (where the stdlib `crypt` module passlib depends on is gone).

**python-jose** — JWT encoding/decoding. HS256, shared secret from env.

**stripe** (Python SDK) — all Stripe API calls wrapped in `asyncio.to_thread` so the sync SDK doesn't block the event loop.

**sendgrid** — transactional email.

## Frontend (web)

**Next.js 16 + React 19** — App Router, server components for static pages, client components for interactive flows. Chosen for SSR + SEO + the mobile-path it opens via React Native. Note: `next lint` was removed as a CLI subcommand in Next 16; lint runs via `eslint .` against the flat config that `create-next-app` generates.

**TypeScript 5** — strict mode, no `any` unless genuinely unavoidable.

**TailwindCSS v4** — utility CSS. v4 uses a single `@import "tailwindcss"` in `globals.css` instead of the three `@tailwind base/components/utilities` directives from v3. `@layer base {}` still works for custom base styles.

**React Query v5** — server state. `/auth/me` is the canonical session source. `staleTime: 30s` avoids flapping.

**Stripe Elements** (`@stripe/react-stripe-js` + `@stripe/stripe-js`) — card collection for PAYG top-ups and subscription checkout. Card data never touches our server (PCI SAQ A-EP).

## Database

**Postgres 16** — relational, JSON-capable if we need flexible metadata later. Cloud SQL in prod; `postgres:16-alpine` in docker-compose.

## Hosting (production)

**Google Cloud Run** — containerized, autoscaling, pay-per-request. Two services per env (`carddroper-backend`, `carddroper-frontend`). `min-instances=1` to avoid cold-start latency.

**Google Cloud SQL (Postgres 16)** — managed Postgres. `db-g1-small` to start in each of staging and prod.

**Google Artifact Registry** — Docker image storage, one repo per env project.

**Google Secret Manager** — runtime secrets (database URL, JWT secret, Stripe keys, SendGrid key). Never in the repo, never in env var flags.

**Google Cloud Build** — CI/CD. One trigger per env (main → staging; `v*` tag → prod). Runs build → migrate → deploy in sequence.

## Local development

**Docker Compose** — Postgres + backend + frontend, one `docker-compose up`. Port 5433 for Postgres to avoid clashing with any local Postgres installation.

## Version control and review

**GitHub** — monorepo: `/backend`, `/frontend`, `/doc`, `/mobile` (future). Branching:
- `dev` — working branch (current).
- `main` — auto-deploys to staging.
- `v*` tags — auto-deploy to prod.
- Feature branches off `dev`, PR back to `dev`, batched to `main` when a release is cut.

**Cloud Build secrets** — mirror Stripe/SendGrid test-mode keys into staging-only secrets; live keys only in prod secrets.

## What we're not using (on purpose)

- **Firebase / Supabase** — vendor lock-in, harder to reason about our own data model.
- **Redis** — not needed at this scale; slowapi works in-memory per instance.
- **Kubernetes** — Cloud Run covers scaling with a fraction of the operational overhead.
- **GraphQL / tRPC** — REST + OpenAPI + TypeScript types is enough and keeps client portability (mobile later).
- **Server-side Stripe Checkout (hosted page)** — Elements gives better UX with the same PCI scope.
- **Playwright / Chromium in the Dockerfile** — foodapp needed this for scraping; carddroper doesn't. Our backend image should be small.
