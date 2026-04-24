# Backend API Reference

> Endpoint catalogue. Filled in as endpoints are built. Each entry should cover: path, method, auth requirement, request body, response body, error conditions, rate limit.
>
> For now this is a planned surface area.

## Auth (`/auth`)

Responses for register, login, refresh, and me include `expires_in: int` (seconds until access token expires), following OAuth 2.0 RFC 6749 §5.1. `/auth/me` returns an envelope `{user, expires_in}` rather than a flat user object.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/register` | none | Create account, send verification email. Response: `{access_token, refresh_token, user, expires_in}`. |
| POST | `/auth/login` | none | Exchange email+password for access + refresh tokens. Response: `{access_token, refresh_token, user, expires_in}`. |
| POST | `/auth/logout` | refresh (cookie or body) | Revoke refresh token, clear cookies. |
| POST | `/auth/refresh` | refresh (cookie or body) | Mint new access token. Response: `{message, access_token, expires_in}`. |
| GET  | `/auth/me` | access | Return current user profile + token TTL. Response: `{user, expires_in}`. |
| PUT  | `/auth/password` | access | Change password (revokes old tokens). |
| POST | `/auth/forgot-password` | none | Send password reset email. |
| GET  | `/auth/validate-reset-token` | none | Check reset token validity without consuming it. |
| POST | `/auth/reset-password` | none | Reset password using token. |
| POST | `/auth/verify-email` | none | Verify email using signed token. |
| POST | `/auth/resend-verification` | access | Send a fresh verification email. |
| POST | `/auth/change-email` | access | Request email change — sends verification link to the new address. |
| POST | `/auth/confirm-email-change` | none | Complete email change using signed token; notifies old address. |

## Billing (`/billing`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/billing/webhook` | Stripe signature (`stripe-signature` header) | Receive and process Stripe webhook events. Dispatches to handlers registered via `EVENT_HANDLERS` registry; currently handles `payment_intent.succeeded`. Unregistered event types are logged and recorded in `stripe_events`; no error returned. Returns 200 on success (including idempotent replays of the same `event.id`). Returns 400 on invalid or missing signature. Only mounted when `BILLING_ENABLED=true`. |
| POST | `/billing/topup` | access + verified; rate-limited (10/min per IP) | Create Stripe PaymentIntent for a PAYG topup. Request: `{amount_micros: int}` (must be in `[BILLING_TOPUP_MIN_MICROS, BILLING_TOPUP_MAX_MICROS]`). Response: `{client_secret: str, amount_micros: int}`. Lazily creates a Stripe Customer if the user has none. Idempotency key scoped to user + amount + minute window. |
| GET  | `/billing/balance` | access (not verified-gated) | Return current balance. Response: `{balance_micros: int, formatted: str}`. Sums `balance_ledger` for the authenticated user. |
| POST | `/billing/portal-session` | access + verified | Create Stripe Customer Portal session. |
| GET  | `/billing/pricing` | none | Return available subscription tiers. |

## Credits (`/credits`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/credits/balance` | access | Current credit balance. |
| POST | `/credits/purchase` | access + verified | Create a Stripe PaymentIntent for a PAYG top-up. |
| GET  | `/credits/history` | access | Paginated ledger entries for the current user. |

## Subscriptions (`/subscriptions`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/subscriptions` | access + verified | Start a subscription. |
| DELETE | `/subscriptions` | access + verified | Cancel at period end (delegates to Stripe). |
| GET  | `/subscriptions/me` | access | Current subscription state. |

## Meta

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/health` | none | DB connectivity + readiness. |

---

Each row above is a placeholder. As endpoints land, expand that row into a full section with:
- Request schema (Pydantic model or JSON example)
- Response schema
- Error conditions (401, 403, 404, 409, 422, 429)
- Rate limit
- Side effects (DB writes, Stripe calls, emails sent)
