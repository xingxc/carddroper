# Payments

## Model

**Usage-based (pay-as-you-go, PAYG) is the default. Subscriptions are optional.** Signup is free; nobody is asked for a card until they choose to pay.

This is a fundamental departure from foodapp, where registration required a payment method and a subscription. Carddroper decouples account from payment.

## User states

| State | Can log in | Can read account pages | Can buy credits / subscribe | Can "send" (paid action) |
|---|---|---|---|---|
| Registered, unverified | ✓ | ✓ | ✗ | ✗ |
| Verified, no credits, no sub | ✓ | ✓ | ✓ | ✗ (unless free tier exists later) |
| Verified, with credits | ✓ | ✓ | ✓ | ✓ (deducts credits) |
| Verified, with active subscription | ✓ | ✓ | ✓ | ✓ (uses quota; overage deducts credits) |

## Stripe resources

- **Customer** — created at signup for every user, stored as `users.stripe_customer_id`. No charges, no payment methods yet.
- **PaymentMethod** — attached when the user first tops up credits or starts a subscription.
- **PaymentIntent** — one-shot charges for PAYG credit purchases.
- **Product + Price** — one or more Stripe Products representing subscription tiers (e.g. "Starter — 100 sends/mo", "Pro — 1000 sends/mo"). Kept simple in v1 — exact tiers TBD.
- **Subscription** — optional, one per user, recurring monthly.

## UX split (matches foodapp's pattern)

| Action | Surface | Where the user is |
|---|---|---|
| Initial PAYG top-up | Embedded **Stripe Elements** (`<PaymentElement>` or `<CardElement>`) | carddroper.com |
| Initial subscription signup | Embedded **Stripe Elements** | carddroper.com |
| Update payment method | **Stripe Customer Portal** (redirect) | billing.stripe.com |
| Cancel subscription | **Stripe Customer Portal** (redirect) | billing.stripe.com |
| View / download invoices | **Stripe Customer Portal** (redirect) | billing.stripe.com |
| Change billing details | **Stripe Customer Portal** (redirect) | billing.stripe.com |

**Why the split.** Entering a card is conversion-critical; redirecting hurts conversion, so we use Elements (card data goes browser → Stripe; our server only sees the `PaymentMethod` ID). Managing billing is rare back-office work; the Portal saves us maintaining screens that Stripe updates for free (Apple Pay, 3DS, new tax rules). If we later want a custom cancellation / retention flow, we build only that single page in-app and keep the Portal for everything else.

Card data never touches our server on either path. PCI scope stays **SAQ A** (the narrowest questionnaire).

**Cloudflare caveat:** when proxy mode is enabled, ensure the Portal's `return_url` (`https://carddroper.com/billing/return`) isn't behind a WAF challenge — Stripe's 302 redirect must complete cleanly.

## Currency

All `credit_ledger.amount` values are **integer USD cents**. No floats, no currency column.

Carddroper is **USD-only** in v1. International users pay in USD; their issuing bank handles FX. Stripe PaymentIntents always use `currency: 'usd'`. Revisit multi-currency only if international signups are a material share of traffic.

## Local data model

```sql
subscriptions (
    id                      SERIAL PRIMARY KEY,
    user_id                 INT NOT NULL REFERENCES users(id) UNIQUE,
    stripe_subscription_id  VARCHAR(64) UNIQUE NOT NULL,
    stripe_price_id         VARCHAR(64) NOT NULL,
    tier_key                VARCHAR(32),           -- e.g. "starter" / "pro"; mirrors Stripe lookup_key
    status                  VARCHAR(32) NOT NULL,  -- trialing/active/past_due/cancelled
    included_quota          INT NOT NULL,          -- sends included per period
    current_period_start    TIMESTAMP,
    current_period_end      TIMESTAMP,
    created_at              TIMESTAMP DEFAULT now(),
    updated_at              TIMESTAMP DEFAULT now()
);

-- Append-only ledger. Current credit balance = SUM(amount) WHERE user_id = ?.
credit_ledger (
    id               BIGSERIAL PRIMARY KEY,
    user_id          INT NOT NULL REFERENCES users(id),
    amount           INT NOT NULL,      -- positive = grant, negative = debit
    reason           VARCHAR(32) NOT NULL, -- 'purchase' | 'send' | 'subscription_quota' | 'subscription_period_reset' | 'refund' | 'adjustment'
    stripe_event_id  VARCHAR(64) NULL,  -- for idempotency of webhook-driven grants
    ref_type         VARCHAR(32) NULL,  -- e.g. 'send_id', for linking debits to specific actions
    ref_id           VARCHAR(64) NULL,
    created_at       TIMESTAMP DEFAULT now()
);
CREATE INDEX ON credit_ledger(user_id, created_at);
CREATE UNIQUE INDEX ON credit_ledger(stripe_event_id) WHERE stripe_event_id IS NOT NULL;

-- Track processed Stripe webhook events for idempotency.
stripe_events (
    id               VARCHAR(64) PRIMARY KEY,  -- Stripe event.id
    event_type       VARCHAR(64) NOT NULL,
    processed_at     TIMESTAMP NOT NULL DEFAULT now()
);
```

Exact schema lands in Alembic; this is documentation.

## Flows

### 1. Signup — create Stripe Customer

`POST /auth/register` →
1. Create `users` row.
2. Create Stripe Customer (email, metadata `user_id`, no payment method).
3. Store `stripe_customer_id` on the user.
4. Send verification email.

No card, no charges.

### 2. PAYG credit purchase

`POST /credits/purchase { amount_usd }` →
1. Require verified user.
2. Create a Stripe PaymentIntent for `amount_usd * 100` cents on the user's Customer.
3. Insert a *pending* `credit_ledger` row with `amount=0`, `reason='purchase'`, `stripe_event_id=NULL` (we'll write the real row on webhook).
4. Return `client_secret` to the frontend.

Frontend confirms the intent with Stripe Elements. Stripe fires `payment_intent.succeeded`. Webhook handler:
1. Look up `stripe_events` by event id — return 200 if already processed (idempotency).
2. Write `credit_ledger` row: `amount = credits_purchased`, `reason='purchase'`, `stripe_event_id=event.id`.
3. Record event in `stripe_events`.

Credits are available immediately.

### 3. Spending credits (a "send")

Inside a DB transaction:
1. Check `SUM(amount)` from `credit_ledger` where `user_id`.
2. If balance < cost, fail.
3. Insert `credit_ledger` row with `amount = -cost`, `reason='send'`, `ref_type='send_id'`, `ref_id=<...>`.

Atomic, auditable, explains every change in balance.

### 4. Subscription sign-up (optional)

`POST /subscriptions { price_id, payment_method_id }` →
1. Require verified user.
2. Attach payment method to the customer, set as default.
3. Create Stripe Subscription.
4. Upsert local `subscriptions` row.
5. Insert `credit_ledger` row granting `included_quota` credits with `reason='subscription_quota'` and `stripe_event_id` set to the subscription creation event.

Each billing period, webhook `invoice.paid` triggers a `subscription_period_reset` ledger entry that zeroes any remaining subscription-granted credits and grants the new period's `included_quota`. (We keep PAYG credits separate by using `reason='purchase'` vs `reason='subscription_quota'` — see "overage" below.)

### 5. Overage handling

When a user with an active subscription runs out of their included quota:
- If they have PAYG credits, debits continue from the PAYG balance.
- If they have no PAYG credits, the `send` action fails with a clear "out of credits, top up or wait for period reset" error.

We intentionally do *not* auto-charge for overages in v1. Users explicitly top up — zero billing surprises.

### 6. Subscription lifecycle (webhooks)

| Event | Action |
|---|---|
| `customer.subscription.created` | Upsert `subscriptions`. Grant `included_quota` credits. |
| `customer.subscription.updated` | Update status, period bounds, tier. |
| `customer.subscription.deleted` | Mark `status='cancelled'`. Do not revoke already-granted credits. |
| `invoice.paid` | If subscription invoice, reset period and grant new `included_quota`. |
| `invoice.payment_failed` | Mark subscription `past_due`. Send notification email. |
| `payment_intent.succeeded` | PAYG: grant credits in ledger. |

All handlers check `stripe_events` first for idempotency.

## Customer Portal

For managing payment methods, invoices, and cancelling subscriptions: `POST /billing/portal-session` returns a Stripe Customer Portal URL. We don't build our own billing UI in v1.

## Why this design

- **Free signup** removes the friction foodapp has; users can explore carddroper and verify email before any money question.
- **Credit ledger is append-only.** Every balance change is traceable. Bugs don't corrupt balance — they leave a visible entry.
- **Idempotent webhook handling** is non-negotiable because Stripe retries. `stripe_events` + unique index on `credit_ledger.stripe_event_id` makes double-processing impossible.
- **PAYG separate from subscription quota** lets us grow more pricing models later (roll-over, tier upgrades, gift credits) without schema changes.
- **No metered Stripe billing in v1.** We can revisit when we see real usage patterns; for now the ledger-in-our-DB model is simpler, cheaper, and user-visible.

## Open design questions

- **Credit denomination value.** Shape is locked: 1 credit = 1 send, priced in round USD cents. The specific cents number depends on SendGrid's per-email cost + our margin — finalize once the SendGrid account is live and we know per-email cost.
- **Subscription grace period** on payment failure — Stripe's default 3-week dunning flow is probably fine; confirm during implementation.
- **Refund path** for PAYG credits — refund via Stripe + negative ledger entry, or disable refunds and handle case-by-case?
- **Tax** — Stripe Tax ($0.50/invoice, handles US sales tax + VAT) from day one, or stay tax-exempt until we cross a US state nexus?
