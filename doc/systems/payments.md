# Payments (chassis subsystem)

> Chassis-level design for Stripe-integrated balance and subscription payments.
> Agnostic to any specific product. Project layers configure tiers, display
> copy, and what actions debit the balance. This is the third chassis
> subsystem after auth and email.

## Chassis boundary

| Layer | Owns | Example |
|---|---|---|
| **Chassis** | Stripe Customer lifecycle, append-only balance ledger, topup + subscribe endpoints, webhook handling (idempotency + signature verification), Customer Portal integration, balance query, grant/debit primitives, display-format policy | `POST /billing/topup`, `POST /billing/subscribe`, `billing.debit(user, 400, ref_type='send', ref_id='123')` |
| **Project** | What actions exist, what each costs in micros, display copy for those actions, tier count and prices in Stripe Dashboard, pricing-page content, free-form vs preset topup UI, optional bonus amounts | A project's `send()` handler calls `billing.debit(user, 400, 'send', send_id)` — a $0.0004 per-recipient cost |

The chassis has **zero knowledge** of what the project sells. Future projects
inherit this subsystem and wire their own actions on top of the debit primitive.

## Denomination — gift-card model (Model B)

The chassis supports two subscription modes controlled by `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER`:

- **Credit-based mode (default for carddroper)** — `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=true`. Subscription = balance grant. Each billing period grants `grant_micros` to `balance_ledger` via `subscription_grant` (initial) and `subscription_reset` (renewals). Adopters (like carddroper) who sell credits opt in by setting the flag true. Topups + subscription grants combine into a spendable USD balance — the gift-card model below.
- **Flat-fee mode (chassis default)** — `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=false`. Subscription = access tier only. `balance_ledger` is never written by subscription events. Adopters using Netflix/Slack-style "pay for access" leave the flag at its default. Topups still work; subscription events just don't add to balance.

Users see a USD balance and spend USD. No abstract "credits." Gift-card mental
model (Starbucks app, Amazon gift card): load money, see balance, spend down.

### Precision: micro-dollars (bigint)

**1 USD = 1,000,000 micros.** Ledger stores `BIGINT amount_micros`.

Rationale:
- **Sub-cent action costs representable.** Email at $0.0004 = 400 micros.
  Cent-precision can't express this without rounding-based bias.
- **Industry-standard.** AWS, GCP, Twilio, SendGrid all bill internally in
  micro-units. Integration at the boundary is natural.
- **Integer arithmetic.** Fast, exact, no float-rounding bugs.
- **Stripe conversion is trivial.** Stripe API uses cents: `cents = micros / 10_000`,
  `micros = cents * 10_000`. Conversion happens at the API boundary only.
- **Headroom.** BIGINT fits ~9.2 trillion USD per row.

### Display policy

Chassis exposes `billing.format_balance(micros) -> str`:
- `>= $0.01`: rounded to 2 decimals → `"$1.23"`
- `0 < micros < $0.01`: 4 decimals → `"$0.0034"` (so users don't see "$0.00" when they have sub-cent balance)
- `= 0`: `"$0.00"`

Default covers US/EN. Projects override via a config hook for localized display.

### Balance invariants

- Balance = `SUM(balance_ledger.amount_micros) WHERE user_id = ?`.
- Balance floor is **zero** — the `debit` primitive raises `InsufficientBalanceError` rather than letting balance go negative. Debits and checks happen inside the caller's DB transaction so a concurrent debit can't underflow.
- `refund` and `adjustment` ledger entries can be any sign; they're admin-initiated. They still cannot drive live balance below zero (a refund is typically the inverse of a prior topup).

### Currency

USD-only in v1. `BILLING_CURRENCY=usd` is the only supported value. Multi-currency
is a future chassis extension (add `currency` column to ledger + subscriptions;
require per-user currency selection) — not in v1 scope.

## Stripe resources

- **Customer** — created on `POST /auth/register` (when billing is enabled), stored as `users.stripe_customer_id`. No payment method, no charges until the user acts.
- **PaymentMethod** — attached on first topup or subscribe.
- **PaymentIntent** — one-shot charge for topups.
- **Product + Price** — subscription tiers. Chassis reads `Price.lookup_key` + `Price.metadata.grant_micros` + `Price.metadata.tier_name` to know what each tier grants on each period. Projects define tiers in Stripe Dashboard, not in chassis code.
- **Subscription** — optional, one per user, recurring.

## UX split

| Action | Surface | Where the user is |
|---|---|---|
| First topup | Embedded **Stripe Elements** | project origin |
| First subscribe | Embedded **Stripe Elements** | project origin |
| Update payment method | **Stripe Customer Portal** (redirect) | billing.stripe.com |
| Cancel subscription | **Stripe Customer Portal** | billing.stripe.com |
| View / download invoices | **Stripe Customer Portal** | billing.stripe.com |
| Change billing details | **Stripe Customer Portal** | billing.stripe.com |

Card data never touches the project server — Elements collects directly to
Stripe, returning a `PaymentMethod` ID. PCI scope stays **SAQ A**.

Subscription cancellation is always **cancel-at-period-end** (Stripe default,
Portal-managed). User retains access through `current_period_end`; chassis
does not build custom cancellation UI.

## Data model (chassis schema)

```sql
subscriptions (
    id                      SERIAL PRIMARY KEY,
    user_id                 INT NOT NULL REFERENCES users(id) UNIQUE,
    stripe_subscription_id  VARCHAR(64) UNIQUE NOT NULL,
    stripe_price_id         VARCHAR(64) NOT NULL,
    tier_key                VARCHAR(64) NOT NULL,   -- mirrors Stripe Price lookup_key
    tier_name               VARCHAR(64) NOT NULL,   -- mirrors Price.metadata.tier_name at subscribe + on customer.subscription.updated; avoids a Stripe API call on every GET /billing/subscription
    status                  VARCHAR(32) NOT NULL,   -- trialing | active | past_due | cancelled | incomplete
    grant_micros            BIGINT NOT NULL,        -- per-period balance grant, mirrored from Price metadata at subscribe time
    current_period_start    TIMESTAMP,              -- naive UTC; single-source-of-truth model (ticket 0024.5): written by POST /billing/subscribe at creation (from Stripe SDK API response where current_period_* is reliably top-level); updated on renewal by invoice.paid (subscription_cycle) from invoice.lines.data[0].period.start (invoice schema is stable across Stripe API versions). customer.subscription.created writes it on INSERT only (rare out-of-band case); customer.subscription.updated does NOT touch it — webhook payload period extraction is unreliable across API versions.
    current_period_end      TIMESTAMP,              -- naive UTC; same single-source-of-truth model as current_period_start. Used by GET /billing/subscription response and cancel-at-period-end UX (displays next billing date / access-through date).
    cancel_at_period_end    BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMP DEFAULT now(),
    updated_at              TIMESTAMP DEFAULT now()
);

-- Append-only. Current balance = SUM(amount_micros) WHERE user_id = ?.
balance_ledger (
    id               BIGSERIAL PRIMARY KEY,
    user_id          INT NOT NULL REFERENCES users(id),
    amount_micros    BIGINT NOT NULL,         -- positive grant, negative debit
    reason           VARCHAR(32) NOT NULL,    -- chassis-closed vocabulary (below)
    stripe_event_id  VARCHAR(64) NULL,        -- idempotency key for webhook-driven grants
    ref_type         VARCHAR(32) NULL,        -- project-layer debit identifier ('send', 'api_call', etc.)
    ref_id           VARCHAR(64) NULL,        -- project-layer action ID
    created_at       TIMESTAMP DEFAULT now()
);
CREATE INDEX ON balance_ledger(user_id, created_at);
CREATE UNIQUE INDEX ON balance_ledger(stripe_event_id) WHERE stripe_event_id IS NOT NULL;

-- Webhook idempotency. Every processed event.id is recorded here.
stripe_events (
    id            VARCHAR(64) PRIMARY KEY,     -- Stripe event.id
    event_type    VARCHAR(64) NOT NULL,
    processed_at  TIMESTAMP NOT NULL DEFAULT now()
);
```

### Reason vocabulary (chassis-closed)

`balance_ledger.reason` is a closed set. Project layers **do not** add reason
values; project-specific debits are identified by `ref_type` + `ref_id`, not
by reason.

| Reason | Sign | Triggered by |
|---|---|---|
| `topup` | + | `payment_intent.succeeded` for a user-initiated purchase |
| `subscription_grant` | + | `customer.subscription.created` (initial period grant) |
| `subscription_reset` | + | `invoice.paid` for subscription renewal (grants new period's `grant_micros`). **V1 simplification:** does not zero remaining prior-period balance; balance increases monotonically across renewals. Strict "zero prior-period remainder + grant new period" semantics are a future hardening (analogous to the cancel-at-period-end-vs-immediate distinction). |
| `signup_bonus` | + | User registration (opt-in, off by default) |
| `verify_bonus` | + | Email verification (opt-in, off by default) |
| `debit` | − | Project-layer action via `billing.debit()` |
| `refund` | − | Admin-issued refund (typically inverts a prior topup) |
| `adjustment` | ± | Admin manual correction (audit trail) |

## Subscription tier contract

Projects define tiers in Stripe Dashboard. The chassis reads the Price
metadata to learn what each tier grants. **Required per Price:**

- `lookup_key` (Stripe top-level field, e.g., `"starter_monthly"`) — stable identifier the chassis cross-references.
- `metadata.grant_micros` (string of int, e.g., `"5000000"`) — balance granted per billing period. **Required when `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=true`; optional (unused) when false.** Flat-fee adopters do not need to set this on their Stripe Prices.
- `metadata.tier_name` (e.g., `"Starter"`) — display name. **Always required** (used in `subscriptions.tier_name` and `GET /billing/tiers` regardless of mode).

Chassis does not hardcode tier count, prices, or grant amounts. A one-tier or
ten-tier structure works identically. Adding/removing/renaming/repricing tiers
is a Stripe Dashboard action, not a chassis migration.

**Runtime resolution via `GET /billing/tiers`.** The chassis pulls `tier_name`,
`grant_micros`, and `Product.description` at request time via `GET /billing/tiers`.
Project layer supplies only a list of `lookup_keys`; the chassis calls
`stripe.Price.list(lookup_keys=..., active=True, expand=["data.product"])` in a
single round-trip and formats display strings via `format_price()`. Stripe
Dashboard becomes the single source of truth for tier name, description, price,
currency, interval, and grant amount — no code change required when prices change.

**Monthly-only in v1.** The schema supports any interval (Stripe drives
`current_period_start`/`end` from the Price's `recurring.interval`), but the
chassis hasn't been exercised against annual/weekly intervals. Chassis code is
interval-agnostic; validation happens via Stripe. Annual billing is a future
extension that likely requires no chassis changes — just a new Stripe Price.

## Flows

### 1. Signup — create Customer

Already owned by auth chassis (`POST /auth/register`). When `BILLING_ENABLED=true`:

1. Register handler creates `users` row.
2. Calls `billing.create_customer(user)` → Stripe Customer created with `email` + `metadata.user_id`.
3. Stores `stripe_customer_id` on the user.
4. If `BILLING_SIGNUP_BONUS_MICROS > 0`, grants bonus via ledger (reason `signup_bonus`).
5. Sends verification email.

When `BILLING_ENABLED=false`, the register handler skips all Stripe calls.
Enabling billing later requires a backfill job (create Customers for existing users).

### 2. Topup (PAYG purchase)

`POST /billing/topup { amount_micros }` →

1. Verified-gate posture (see §Verified-gate posture): permissive by default (`BILLING_REQUIRE_VERIFIED=false`); set `BILLING_REQUIRE_VERIFIED=true` to restore the verified-only gate.
2. Reject if `amount_micros` outside `[BILLING_TOPUP_MIN_MICROS, BILLING_TOPUP_MAX_MICROS]`.
3. Create Stripe PaymentIntent for `amount_micros / 10_000` cents on the user's Customer.
4. Return `{client_secret}` to frontend.

Frontend confirms via Stripe Elements. Stripe fires `payment_intent.succeeded`.
Webhook handler:

1. Check `stripe_events` by `event.id` — return 200 if already processed.
2. Write `balance_ledger`: `+amount_micros`, `reason='topup'`, `stripe_event_id=event.id`.
3. Record event in `stripe_events`.

Balance is live immediately after webhook. Frontend polls `GET /billing/balance`
or invalidates React Query on Stripe confirmation to refresh display.

**Preset + free-form UX.** Chassis endpoint accepts any `amount_micros` in the
configured range. Project UI can render preset buttons (`$5`, `$20`, `$50`), a
free-form input, or both side-by-side. Preset amounts are a project-layer
config, not a chassis field.

### 3. Debit (project-layer action)

Project code calls the chassis primitive directly, inside the project's own DB transaction:

```python
await billing.debit(
    user_id=user.id,
    amount_micros=400,          # $0.0004
    ref_type="send",             # project-specific
    ref_id=str(send.id),
    db=db,
)
```

Implementation:
1. `SELECT COALESCE(SUM(amount_micros), 0) FROM balance_ledger WHERE user_id=?` (within txn).
2. If `balance < amount_micros`, raise `InsufficientBalanceError`.
3. Insert `balance_ledger`: `-amount_micros`, `reason='debit'`, `ref_type`, `ref_id`.

The chassis never initiates debits itself. All debits are driven by
project-layer actions calling this primitive.

### 4. Subscribe

`POST /billing/subscribe { price_lookup_key, payment_method_id }` →

1. Verified-gate posture (see §Verified-gate posture): permissive by default (`BILLING_REQUIRE_VERIFIED=false`); set `BILLING_REQUIRE_VERIFIED=true` to restore the verified-only gate.
2. Attach `payment_method_id` to the Customer, set as default.
3. Resolve Stripe Price from `lookup_key`. Read `metadata.tier_name` (always required). Read `metadata.grant_micros` only when `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=true` (422 if missing in that mode; ignored when false).
4. Create Stripe Subscription with `automatic_tax.enabled=STRIPE_TAX_ENABLED`.
5. Upsert `subscriptions` row (keyed on `user_id`; one active subscription per user in v1).
6. **When `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=true`:** grant `subscription_grant`: `+grant_micros` ledger entry deferred to `customer.subscription.created` webhook. **When false:** subscription row is upserted; `balance_ledger` is not written.

### 5. Subscription lifecycle (webhooks)

| Event | Action |
|---|---|
| `customer.subscription.created` | Upsert subscription row. **When `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=true`:** also grant `subscription_grant`. When false: row only, no ledger write. |
| `customer.subscription.updated` | Update `status`, `current_period_end`, `cancel_at_period_end`, `tier_key`, `grant_micros` (re-read from Price metadata). No ledger write regardless of flag. |
| `customer.subscription.deleted` | Mark `status='cancelled'`. Do NOT revoke already-granted balance. |
| `invoice.paid` | If subscription renewal: update period timestamps. **When `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=true`:** also post `subscription_reset` entries. When false: timestamps updated only, no ledger write. |
| `invoice.payment_failed` | Mark subscription `past_due`. Balance stays spendable. |
| `payment_intent.succeeded` | Topup: grant `topup`. |
| `charge.refunded` | Record `refund` ledger entry (negative). |

All handlers:
1. Verify Stripe webhook signature (fail if invalid).
2. Check `stripe_events` for idempotency (return 200 early if already processed).
3. Perform the side effect.
4. Insert `stripe_events` row.

**Idempotency mechanism — atomic INSERT … ON CONFLICT.** The chassis uses an atomic `INSERT INTO stripe_events … ON CONFLICT (id) DO NOTHING` (via SQLAlchemy's `pg_insert`) rather than a SELECT-then-INSERT check. Postgres serialises concurrent INSERTs on the same `id` at the row-lock level, so exactly one transaction "owns" the event id and the handler runs exactly once — even when Stripe delivers the same event id to two concurrent requests (duplicate delivery, retry-while-processing, or multiple webhook endpoint configs). A race-loser (rowcount=0) returns 200 without re-invoking the handler. If the handler raises an uncaught exception the entire transaction rolls back, including the `stripe_events` INSERT, so Stripe sees a 5xx and retries; the retry then gets rowcount=1 and runs the handler again. All future webhook handlers registered in `EVENT_HANDLERS` inherit this guarantee at the route level without any per-handler changes.

### 6. Past-due behavior

On `invoice.payment_failed`:
- `subscriptions.status = 'past_due'`.
- Balance remains **fully spendable** — prior topups and prior subscription
  grants are money the user already has. They can continue to debit until
  balance hits zero.
- No new subscription grants fire until Stripe's dunning resolves (`invoice.paid`)
  or the subscription is cancelled (`subscription.deleted`).
- Project UI can render a past-due banner + "update card" CTA linking to the
  Customer Portal.

### 7. Cancellation

User cancels via Customer Portal. Stripe fires `customer.subscription.updated`
with `cancel_at_period_end=true`; chassis records the flag. User retains
subscription benefits through `current_period_end`. At period end, Stripe fires
`customer.subscription.deleted`; chassis marks `status='cancelled'`.

Already-granted balance is **never revoked** on cancellation. If the user had
$3 left from this period's subscription grant plus a $20 prior topup, they
keep both.

## Chassis-exposed primitives

Python package `app.billing` (chassis module). Project layers import and call:

```python
async def create_customer(user: User, db: AsyncSession) -> str:
    """Create Stripe Customer. Returns stripe_customer_id. Called by auth chassis at register."""

async def get_balance_micros(user_id: int, db: AsyncSession) -> int:
    """Current balance. Sums balance_ledger. Returns int (never negative)."""

async def grant(
    user_id: int,
    amount_micros: int,
    reason: GrantReason,
    db: AsyncSession,
    *,
    stripe_event_id: str | None = None,
) -> None:
    """Positive ledger entry. `reason` must be a GrantReason enum value."""

async def debit(
    user_id: int,
    amount_micros: int,
    ref_type: str,
    ref_id: str,
    db: AsyncSession,
) -> None:
    """Negative ledger entry. Raises InsufficientBalanceError if balance insufficient.
    Must be called inside the caller's active DB transaction."""

def format_balance(micros: int) -> str:
    """Chassis display policy: '$1.23' or '$0.0034'."""

def format_price(amount_cents: int, currency: str, interval: str, interval_count: int = 1) -> str:
    """Format a Stripe Price as a human-readable display string.

    Examples: '$9.99/month', '$10/month', '$99/year', '$15 every 3 months'.
    Whole-dollar amounts render without decimals. interval_count > 1 uses 'every N <interval>s' form.
    USD-only per chassis BILLING_CURRENCY for v1; non-USD logs warning and uses '$' prefix fallback.
    """
```

Projects **never write to `balance_ledger` directly** — always go through these
primitives so the chassis owns the invariants (non-negative balance, reason
vocabulary, idempotency).

## Chassis-exposed HTTP endpoints

- `GET /billing/balance` — returns `{balance_micros: int, formatted: str}`. Authed.
- `POST /billing/setup-intent` — creates Stripe SetupIntent for collecting a payment method. Returns `{client_secret: str}`. Verified user only. Lazily creates Stripe Customer. Idempotency: one per user per minute.
- `POST /billing/topup` — returns Stripe `client_secret`. Verified user only.
- `POST /billing/subscribe` — creates subscription. Verified user only. Rate-limited.
- `GET /billing/subscription` — returns subscription state `{has_subscription, tier_key, tier_name, status, current_period_end, cancel_at_period_end}`. Authed (not verified-gated).
- `GET /billing/tiers` — returns enriched `TierEnvelope[]` for a CSV `lookup_keys` query param. Calls `stripe.Price.list(lookup_keys=..., active=True, expand=["data.product"])` in one round-trip. Prices missing required metadata are silently skipped. Non-USD logs warning; `$` prefix used as fallback. Response order matches input order. Authed (not verified-gated).
- `POST /billing/portal-session` — returns Stripe Customer Portal URL. Authed.
- `POST /billing/webhook` — Stripe webhook handler. Signature-verified; no auth dep.

## Verified-gate posture

**Chassis default: permissive.** As of 0024.3, the three billing mutation endpoints — `POST /billing/topup`, `POST /billing/subscribe`, `POST /billing/setup-intent` — are **not** verified-gated by default. Any authed user can transact in the same session as signup. This matches the SaaS-industry default (Stripe, Shopify, most B2C SaaS) and reduces abandonment at the moment of highest intent.

**Flag: `BILLING_REQUIRE_VERIFIED: bool = False`** (in `backend/app/config.py`). Setting this to `true` restores the verified-only gate on all three endpoints; unverified users receive `403 FORBIDDEN` with message `"Please verify your email before taking this action."` — the same error format produced by the chassis `require_verified` dep, so existing frontend error-mapping requires no change.

**Scope:** the flag applies to the three billing mutation endpoints only. Read endpoints (`GET /billing/balance`, `GET /billing/subscription`, `GET /billing/tiers`) are authed-only and unaffected. The webhook endpoint is Stripe-signature-authenticated and unaffected. Other non-billing verified gates (if any are added in future) are unaffected.

**Receipt-email deliverability:** when the flag is off, the user's email at Stripe checkout is the address captured at signup (`users.email`). If `verified_at IS NULL`, the address may bounce; bounces appear in the SendGrid Activity Feed and are recoverable by admin follow-up. Chargebacks route through Stripe's own dispute workflow; `verified_at` status does not affect chargeback handling.

## Optional lifecycle bonuses

Chassis supports one-time promotional balance grants on auth lifecycle events.
Both default **OFF**:

- `BILLING_SIGNUP_BONUS_MICROS` — granted on successful `POST /auth/register`, reason `signup_bonus`.
- `BILLING_VERIFY_BONUS_MICROS` — granted on successful `POST /auth/verify-email`, reason `verify_bonus`.

When enabled, the auth chassis calls `billing.grant(...)` conditionally. Projects
decide the amount; chassis provides the hook. Disabling at any later time leaves
past grants intact (ledger is append-only).

**Legal note:** promotional grants are generally not subject to the gift-card
laws that regulate purchased credits. Projects should confirm with counsel
before enabling.

## Tax

**Stripe Tax is a drop-in.** When `STRIPE_TAX_ENABLED=true`, chassis passes
`automatic_tax: {enabled: true}` to all PaymentIntent and Subscription creations.
Stripe computes sales tax / VAT at checkout, displays it to the user, and
handles remittance per jurisdiction.

Chassis code is ~1 line per endpoint. Adopter responsibilities (out of chassis
scope):
- Register Stripe Tax in the Stripe Dashboard.
- Assign tax codes to Stripe Products.
- Monitor US state nexus thresholds.

Approximately $0.50/invoice as of 2026-04.

## Refunds

Chassis exposes:

```python
async def refund(
    topup_ledger_entry_id: int,
    admin_user_id: int,
    reason_note: str,
    db: AsyncSession,
) -> None:
    """Inverts a prior topup. Issues Stripe refund + writes negative ledger entry (reason='refund')."""
```

No user-facing self-service refund endpoint. Projects define their own refund
policy (posted in ToS / FAQ) and back it with calls to this primitive via
admin tooling. Default project posture should be "no self-service refunds"
unless operational needs say otherwise.

## Config

Existing (auth chassis already has these optional):
- `STRIPE_SECRET_KEY` — required when `BILLING_ENABLED=true`.
- `STRIPE_WEBHOOK_SECRET` — required when the webhook endpoint is mounted.

New (add when the billing chassis lands):
- `BILLING_ENABLED` (bool, default `false`) — master switch. When false, no billing endpoints mount, no Stripe Customer creation at signup.
- `BILLING_CURRENCY` (default `"usd"`) — USD-only supported in v1.
- `BILLING_TOPUP_MIN_MICROS` (default `500_000` = $0.50) — minimum topup.
- `BILLING_TOPUP_MAX_MICROS` (default `500_000_000` = $500) — maximum single topup.
- `STRIPE_TAX_ENABLED` (bool, default `false`).
- `BILLING_SIGNUP_BONUS_MICROS` (int, default `0`).
- `BILLING_VERIFY_BONUS_MICROS` (int, default `0`).

When `BILLING_ENABLED=true`, pydantic validators must enforce that
`STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are non-empty. This becomes a
`chassis-contract.md` entry when the billing layer lands.

## Why this design

- **Chassis agnosticism.** No verbs or nouns from any specific product. Next
  project using this chassis gets a complete billing subsystem; they only
  configure tier metadata in Stripe and wire `billing.debit()` into their
  actions.
- **USD balance transparency.** Users see real USD, not abstract credits.
  Matches the gift-card mental model (load money, watch balance decrease).
- **Micro-precision.** Sub-cent action costs are first-class. Integer math
  throughout; no float-rounding bugs; standard industry practice.
- **Append-only ledger.** Every balance change is auditable. Bugs leave
  visible entries rather than corrupting state silently.
- **Idempotent webhooks.** Stripe retries are safe by construction via
  `stripe_events` + unique index on `balance_ledger.stripe_event_id`.
- **Tier structure in Stripe, not code.** Repricing, renaming, adding tiers
  is a Dashboard action. Chassis reads metadata at subscribe time.
- **Project owns vocabulary and UX.** Chassis provides primitives; project
  decides what actions cost, what to name them, and how to render balance.

## Open questions (chassis-level)

None currently. Project-level decisions (tier count, preset topup amounts,
bonus amounts, action pricing) are configuration — not chassis design work.
