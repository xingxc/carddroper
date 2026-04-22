# Carddroper Documentation

> A paperless-post-style web + mobile application. Docs describe architecture, systems, and operations — features are deliberately out of scope until the substrate is complete.

## Start here

- [PLAN.md](PLAN.md) — decision log. What we're building and why. Read this first.

## Architecture

How the system is put together.

| Doc | What it covers |
|---|---|
| [architecture/overview.md](architecture/overview.md) | System diagram, component responsibilities, request flow. |
| [architecture/tech-stack.md](architecture/tech-stack.md) | Each technology choice with rationale and rejected alternatives. |
| [architecture/site-model.md](architecture/site-model.md) | **DECIDED.** Canva-model auth wall: public marketing + auth-gated app. Chassis/body split for reuse across projects. |

## Systems

Deep dives on individual subsystems.

| Doc | What it covers |
|---|---|
| [systems/auth.md](systems/auth.md) | JWT + refresh tokens, email verification, password reset, rate limits, mobile-friendliness. |
| [systems/payments.md](systems/payments.md) | Stripe Customer lifecycle, PAYG credits via Payment Intents, optional subscriptions, credit ledger, webhooks. |

## Operations

Running, deploying, and evolving carddroper.

| Doc | What it covers |
|---|---|
| [operations/development.md](operations/development.md) | Local setup, docker-compose, day-to-day workflow, branching. |
| [operations/environments.md](operations/environments.md) | dev / staging / prod layout, promotion path, secret strategy. |
| [operations/deployment.md](operations/deployment.md) | GCP Cloud Run + Cloud SQL + Cloud Build playbook (filled as we stand each env up). |
| [operations/testing.md](operations/testing.md) | Three-tier testing policy — local / staging / prod — with per-ticket coverage checklist and smoke-script pattern. |

## Legal

| Doc | What it covers |
|---|---|
| [legal/terms-of-service.md](legal/terms-of-service.md) | **DRAFT** — starting point, requires attorney review before launch. |
| [legal/privacy-policy.md](legal/privacy-policy.md) | **DRAFT** — starting point, requires attorney review before launch. |

## Reference

| Doc | What it covers |
|---|---|
| [reference/backend-api.md](reference/backend-api.md) | Endpoint catalogue. Filled in as routes are built. |
