# Architecture Overview

## The shape of the system

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│   Browser (Next.js 15 app)   │         │   Mobile (Expo, deferred)    │
│   - cookies for web          │         │   - Bearer + Keychain        │
└──────────────┬───────────────┘         └──────────────┬───────────────┘
               │                                        │
               └────────────────┬───────────────────────┘
                                │  HTTPS (JSON, JWT)
                                ▼
                    ┌─────────────────────────┐
                    │   FastAPI backend       │ ← Cloud Run
                    │   Python 3.12, async    │
                    │                         │
                    │  routes/auth            │
                    │  routes/billing         │
                    │  routes/profile         │
                    └────┬────────────┬───────┘
                         │            │
                ┌────────▼──────┐  ┌──▼──────────────┐
                │ Postgres 16   │  │ External        │
                │ (Cloud SQL)   │  │  - Stripe       │
                │               │  │  - SendGrid     │
                └───────────────┘  └─────────────────┘
```

## Separation of concerns

**Browser / mobile client** — renders UI, holds session via HttpOnly cookies (web) or Bearer tokens in Keychain / EncryptedSharedPreferences (mobile). Never talks to Postgres or Stripe directly.

**FastAPI backend** — the single source of truth. Handles all authentication, business logic, database access, and third-party API calls. Serves a REST-ish JSON API that every client (web now, mobile later) consumes identically.

**Postgres** — all persistent state. Users, refresh tokens, email verification tokens, Stripe linkage, credit ledger, subscription state.

**Stripe** — payment processing. The backend mirrors Stripe state into Postgres via webhooks so business logic doesn't have to round-trip Stripe for every read.

**SendGrid** — transactional email (verification, password reset, payment receipts).

## Request flow — a typical authenticated call

1. Client sends `POST /credits/purchase` with an `access_token` cookie (web) or `Authorization: Bearer <jwt>` header (mobile).
2. FastAPI middleware logs the request, starts a DB session.
3. `get_current_user` dependency extracts and decodes the JWT, verifies `token_version` against the user row, attaches the user to the request.
4. Route handler enforces "email must be verified" (raise 403 otherwise).
5. Handler creates a Stripe Payment Intent, persists a pending entry in the credit ledger, returns the client secret.
6. Client confirms the intent with Stripe Elements. Stripe fires `payment_intent.succeeded` webhook.
7. Webhook handler marks the ledger entry as `succeeded`, increments the user's available balance.
8. Client's next poll (or websocket, later) shows the new balance.

## Why this shape

- **One backend, many clients.** The same JSON API serves the web app today and an Expo mobile app later. No client-specific code paths except cookie vs Bearer, which is already supported by the token extraction layer.
- **Webhooks over polling.** Stripe drives state into our database. This keeps reads fast (no Stripe round trips) and makes the system tolerant of brief Stripe downtime.
- **Stateless backend.** Cloud Run instances can scale horizontally; sessions live entirely in the JWT + refresh token DB rows. No sticky sessions needed.
- **Local ≡ prod topology.** `docker-compose` mirrors the prod topology (Postgres + backend + frontend containers). Only the DB host, Stripe keys, and cookie settings differ.

## What's deliberately absent

- No GraphQL — REST is sufficient and simpler for a JWT-based auth model.
- No websockets in v1 — they can be added for live notifications later.
- No Redis — rate limiting uses in-memory state per Cloud Run instance, which is fine at the scale of "handful of instances." Swap to Redis if we need cross-instance limits.
- No message queue — Stripe webhooks are retried by Stripe itself; no internal queue needed yet.
- No service mesh, no Kubernetes — Cloud Run handles scaling, networking, and TLS without that complexity.
