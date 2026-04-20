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

# Environment files
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
# Fill in JWT_SECRET, Stripe test keys, SendGrid key (or leave blank)

# Bring up everything (alembic migrations run automatically on backend startup)
docker-compose up
```

Frontend: http://localhost:3000
Backend: http://localhost:8000
Backend docs: http://localhost:8000/docs
Postgres: `psql postgresql://carddroper:carddroper@localhost:5433/carddroper`

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
