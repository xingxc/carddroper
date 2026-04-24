# Development

How to work on carddroper locally.

> This doc is a stub. It gets filled in as we scaffold the backend and frontend. The layout below describes the target state.

## Prerequisites

- macOS (Darwin) or Linux.
- Docker Desktop (for `docker-compose`).
- Python 3.11 (for running the backend outside Docker during debugging).
- Node 20 LTS (for running the frontend dev server outside Docker).
- A Stripe test-mode account.
- A SendGrid account (sandbox or real), or skip email sending locally (the backend will log verification links to stdout when SendGrid isn't configured).

## First-time setup

```bash
# Clone
git clone git@github.com:<your-user>/carddroper.git
cd carddroper

# Environment files — three tiers (see §Env-var tiers below for why)
cp .env.example .env                            # root — docker-compose ${VAR} substitutions
cp backend/.env.example backend/.env            # backend container runtime (FastAPI + pydantic-settings)
cp frontend/.env.example frontend/.env.local    # frontend runtime (only used when running npm run dev outside docker-compose)
# Fill in:
#   root .env       — STRIPE_PUBLISHABLE_KEY (pk_test_... from Stripe test mode)
#   backend/.env    — JWT_SECRET (generate via `python -c "import secrets; print(secrets.token_urlsafe(48))"`),
#                     STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET (from `stripe listen`), SendGrid key (or blank)

# Bring up everything (alembic migrations run automatically in Cloud Build; locally, backend startup invokes them)
docker-compose up
```

Frontend: http://localhost:3000
Backend: http://localhost:8000
Backend docs: http://localhost:8000/docs
Postgres: `psql postgresql://carddroper:carddroper@localhost:5433/carddroper`

## Env-var tiers

The project uses three distinct env-var files with non-overlapping audiences. The separation is **forced by the tooling**, not a style preference — knowing which file a var belongs in requires knowing which tool consumes it.

| Tier | File | Consumer | Typical contents |
|---|---|---|---|
| **1. Orchestration** | `.env` at repo root (from `.env.example`) | Docker Compose itself — for `${VAR}` substitutions inside `docker-compose.yml` | `STRIPE_PUBLISHABLE_KEY` (passed as a frontend build-arg; baked into the Next.js JS bundle) |
| **2. Backend runtime** | `backend/.env` (from `backend/.env.example`) | Pydantic-settings inside the FastAPI container, loaded via `env_file: ./backend/.env` directive in docker-compose.yml | `DATABASE_URL`, `JWT_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SENDGRID_API_KEY`, `BILLING_ENABLED`, rate limits, cookie config |
| **3. Frontend runtime** | `frontend/.env.local` (from `frontend/.env.example`) | Next.js at runtime | Empty by default in the docker-compose workflow — `NEXT_PUBLIC_*` values reach the bundle via docker-compose build-args instead. Only relevant when running `npm run dev` outside Docker. |

### Why three tiers — forced by tooling, not preference

- Docker Compose's `${VAR}` substitution engine only reads from the shell environment or a `.env` file **in the same directory as the compose file** (repo root). It **cannot** read `backend/.env` — the `env_file:` directive on a service is a separate mechanism that injects vars into the container at runtime, not into Compose's own substitution engine.
- So any var needed at compose-processing time (e.g., anything passed as a `build.args` substitution) **must** live at root. This is why `STRIPE_PUBLISHABLE_KEY` lives there and not in `backend/.env`, even though it's semantically a "frontend" concern.
- Backend runtime vars live with the backend container's config, not at root, because pydantic-settings reads them directly inside the running container.
- Frontend runtime vars are largely inert in the docker-compose flow because `NEXT_PUBLIC_*` values are build-time-baked, not read at runtime.

### The STRIPE_PUBLISHABLE_KEY pipeline (illustrative — four stages)

```
[stage 1] root .env:                          STRIPE_PUBLISHABLE_KEY=pk_test_...
              ↓ docker-compose ${VAR} substitution at `docker-compose up` time
[stage 2] docker-compose.yml frontend.build.args:
              NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=<resolved value>
              ↓ passed as `--build-arg` during `docker build`
[stage 3] frontend Dockerfile: ARG → ENV in the build container
              ↓ `next build` resolves process.env.NEXT_PUBLIC_*
[stage 4] Next.js JS bundle: literal string baked in
              ↓ browser loads bundle
              runtime: process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY === "pk_test_..."
```

Staging / prod replaces stage 1 with Cloud Build substitution variables (`_STRIPE_PUBLISHABLE_KEY` configured in the trigger). Stages 2–4 are identical.

### When adding a new env var, which tier?

Ask: **at what point in the pipeline is the value consumed?**

- Consumed by Python at backend request time → `backend/.env` + pydantic-settings `Settings` field + `chassis-contract.md` entry if required-for-startup.
- Needs to be baked into the frontend JS bundle at build time → **root `.env`**, plus matching `build.args` line in `docker-compose.yml` and matching `--build-arg` in `cloudbuild.yaml` (Cloud Build substitution variable).
- Consumed by Next.js at frontend runtime (server-side data fetching, runtime config for pages not pre-rendered) → `frontend/.env.local`.
- Consumed by Docker Compose itself (health check intervals, scale counts, etc.) → root `.env`.

Each new env var should show up in exactly one `.env.example` — the tier it belongs to — so fresh clones pick up the template from the right place.

## Day-to-day

```bash
# Start / stop
docker-compose up
docker-compose down            # keep the DB
docker-compose down -v         # nuke the DB too

# Run backend outside Docker for fast iteration
cd backend
source .venv/bin/activate
DATABASE_URL="postgresql+asyncpg://carddroper:carddroper@localhost:5433/carddroper" uvicorn app.main:app --reload

# Same for frontend
cd frontend
npm run dev

# Run tests
cd backend && pytest
cd frontend && npm test
```

## Branching

- Work on `dev`. Commit early.
- Push `dev` to open a PR against `main`.
- Merging to `main` auto-deploys to staging.
- Tagging a commit `v*.*.*` (from `main`) auto-deploys to prod.

Never commit directly to `main` or a release tag.

## Migrations

```bash
# Create a new migration from model changes
cd backend
alembic revision --autogenerate -m "add email verification"

# Review the generated file. Edit if autogenerate missed anything.
# Apply locally:
alembic upgrade head

# Rollback one step:
alembic downgrade -1
```

One migration per PR. Name it for the change, not the ticket number.

## Stripe webhook testing (local)

```bash
# Install Stripe CLI: https://stripe.com/docs/stripe-cli
stripe login
stripe listen --forward-to localhost:8000/billing/webhook
# Copy the "whsec_..." signing secret into backend/.env as STRIPE_WEBHOOK_SECRET
# Trigger test events:
stripe trigger payment_intent.succeeded
stripe trigger customer.subscription.created
```

## Common issues

Filled in as they appear. See foodapp's `docs/known-issues.md` for the template.
