---
id: 0024
title: subscribe + subscription lifecycle handlers (chassis) — /billing/subscribe + /billing/setup-intent + /billing/subscription + 5 webhook handlers + Stripe Elements SubscribeForm
status: open
priority: medium (chassis-completion of the payments subsystem; second user-facing billing surface after PAYG topup; chassis primitive, not project-specific)
found_by: PLAN.md §10.6 Stripe-layer roadmap; sequenced after 0023 PAYG topup (chassis substrate now fully validated by 0017 + 0017.1 + 0018 + 0019).
---

## Context

Second user-facing billing surface, building directly on 0023's substrate. Adds the recurring-billing half of the payments chassis:

1. User picks a Stripe Price tier from project-supplied options (chassis ships SubscribeForm with no built-in tiers).
2. Frontend creates a SetupIntent via `POST /billing/setup-intent`, mounts Stripe Elements, user enters card.
3. `stripe.confirmSetup()` succeeds with a `payment_method_id` attached to the user's Customer.
4. Frontend POSTs `/billing/subscribe { price_lookup_key, payment_method_id }`.
5. Backend resolves the Price by `lookup_key`, reads `metadata.grant_micros` + `metadata.tier_name`, creates Stripe Subscription with `automatic_tax.enabled=settings.STRIPE_TAX_ENABLED`, upserts `subscriptions` row, returns subscription state.
6. Stripe fires `customer.subscription.created` webhook → handler grants `subscription_grant` ledger entry (initial period balance).
7. Each renewal: Stripe fires `invoice.paid` → handler posts `subscription_reset` (zero remaining prior-period grant + grant new period). Cancellation/dunning fire `customer.subscription.updated`/`.deleted` and `invoice.payment_failed` — chassis mirrors state.

**Chassis framing.** Project-agnostic throughout. Tier count, price points, and `grant_micros` per tier all live in Stripe Dashboard Prices metadata — no chassis migration when projects add/rename/reprice tiers. `SubscribeForm` takes `tiers: Tier[]` as a required prop (chassis ships no defaults; project supplies an array sourced from its own pricing config). No carddroper-specific copy.

Full chassis design is in `doc/systems/payments.md`:
- §Subscription tier contract (Price metadata reqs)
- §Flows item 4 (Subscribe), item 5 (Subscription lifecycle webhooks), item 6 (Past-due), item 7 (Cancellation)
- §Data model (`subscriptions` table) and §Reason vocabulary (`subscription_grant`, `subscription_reset`)

This ticket implements those flows verbatim.

### What the chassis substrate already provides (no work in this ticket)

- **Stripe Customer creation at register** — 0021.
- **Webhook signature verification + atomic-INSERT idempotency** — 0021 + 0023.2.
- **Dispatch registry pattern** (`@register("event_type")`) — 0023.
- **`payment_intent.succeeded` handler for topup** — 0023.
- **Frontend Stripe Elements wiring + lib/stripe singleton** — 0023.
- **`useBalance` + `BalanceDisplay` chassis primitives** — 0023.
- **`/app/billing` page hosts TopupForm** — 0023.
- **chassis-contract.md `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` invariants** — 0021.
- **Boot-time fail-loud validators + `extra="forbid"`** — 0018.
- **Dedicated runtime SAs + IAM cleanup** — 0018 follow-up.
- **DKIM/SPF/DMARC for transactional mail** — 0019.

0024 inherits all of this. New webhook handlers register cleanly via the existing dispatch registry; the new endpoints sit alongside `/billing/topup` + `/billing/balance`.

## Design decisions (pre-committed)

Most decisions are already locked in `payments.md`. Items below either reference that spec or capture an explicit choice for areas where payments.md is ambiguous.

1. **Two-step subscribe flow** — SetupIntent → frontend confirms PM → subscribe with `payment_method_id`. Matches `payments.md §Flows item 4` contract verbatim. Chassis adds `POST /billing/setup-intent` (not in `payments.md §Chassis-exposed HTTP endpoints` today; this ticket extends the spec — update payments.md in the same commit chain). Rejected alternative: single-step `default_incomplete` flow (used by 0023's topup) — leaves abandoned subscriptions in `incomplete` status that Stripe auto-cancels after 23 hours; cleaner-but-not-required for chassis purity.

2. **`subscriptions` table schema** — exactly per `payments.md §Data model`. UNIQUE constraint on `user_id` (one active subscription per user in v1). Resubscribe upserts the row; cancellation history lives in Stripe Dashboard, not chassis.

3. **Tier metadata read from Stripe Price** — at subscribe time, read `Price.lookup_key`, `Price.metadata.grant_micros` (string-of-int, parse to int), `Price.metadata.tier_name`. Required keys; if any missing, return 400 to the frontend. **Validation in chassis code** — not as a startup-time invariant (Prices live in Stripe, not in chassis state).

4. **5 new webhook handlers** in `app/billing/handlers/subscription.py`:
   - `customer.subscription.created` — upsert `subscriptions` row + grant initial period via `subscription_grant`.
   - `customer.subscription.updated` — sync state (`status`, `current_period_start`/`end`, `cancel_at_period_end`, `tier_key`, `grant_micros` re-read from Price metadata in case the user upgraded/downgraded).
   - `customer.subscription.deleted` — mark `status='cancelled'`; do NOT revoke already-granted balance.
   - `invoice.paid` — period rollover. Distinguish: if `invoice.billing_reason == 'subscription_create'`, no-op (the `subscription_grant` already fired on `customer.subscription.created`); if `'subscription_cycle'`, post `subscription_reset` (one negative entry zeroing remaining prior-period balance, one positive entry granting new period's `grant_micros`).
   - `invoice.payment_failed` — mark `subscriptions.status='past_due'`. Per `payments.md §Past-due behavior`: balance stays spendable.

5. **Two new GrantReason enum values:** `SUBSCRIPTION_GRANT`, `SUBSCRIPTION_RESET` (both positive on initial grant + new period grant; `subscription_reset` may also include a negative entry to zero remaining prior-period balance).

6. **Subscription cancellation = cancel-at-period-end.** Cancellation is initiated via the Customer Portal (0025), not in 0024. The chassis handles the Stripe-fired `customer.subscription.updated` event with `cancel_at_period_end=true` by recording the flag; user retains access through `current_period_end`. No custom in-app cancellation UI in this ticket.

7. **`SubscribeForm` chassis primitive.** Two-phase form similar to `TopupForm` (0023):
   - **Phase A (select tier)** — render tier cards from `tiers: Tier[]` prop where `Tier = { lookup_key: string; tier_name: string; price_display: string; description?: string; }`.
   - **Phase B (enter card)** — POST `/billing/setup-intent` to get `client_secret`; mount Stripe `<Elements>` with `<PaymentElement>`; on submit call `stripe.confirmSetup({elements, redirect: 'if_required'})`; on success POST `/billing/subscribe { price_lookup_key, payment_method_id }`; poll `/billing/subscription` until `status='active'` or 10s timeout.
   - Required prop `tiers`; chassis ships no defaults. TypeScript catches missing prop.

8. **`useSubscription` + `SubscriptionDisplay` chassis primitives.** Mirror `useBalance` + `BalanceDisplay` (0023). `SubscriptionDisplay` renders current tier + status + next billing date when subscribed; renders "no subscription" state otherwise. No forced placement — projects decide where it appears.

9. **`/app/subscribe` page** — chassis ships this page hosting `<SubscribeForm tiers={[]} />`; project-layer overrides the page to pass real tiers. The empty-tiers state renders "no plans available" guidance. **NOT** a `/app/billing` extension — keeping subscribe and topup as separate surfaces simplifies routing and state. ProfileMenu Settings section gets a "Subscription" link mirroring the Billing link from 0023.

10. **Subscribe endpoint requires verified user.** Same as topup. Unverified users see the form but POST returns 403.

11. **No new env vars.** All Stripe config (secret key, webhook secret, tax flag) was added by 0021/0023. Tier definitions live in Stripe Dashboard.

12. **Webhook event subscription on Stripe Dashboard.** Phase 2 staging deploy adds 5 events (`customer.subscription.{created,updated,deleted}`, `invoice.{paid,payment_failed}`) to the existing webhook endpoint at `https://api.staging.carddroper.com/billing/webhook`. The endpoint already verifies signatures and dedups; only the event-list configuration changes.

13. **Subscribe rate limit: 5/minute per IP** — new `SUBSCRIBE_RATE_LIMIT = "5/minute"` in Settings (configurable). Lower than topup's 10/minute since subscribe is rarer. Same chassis-contract treatment as other rate-limit settings.

14. **Smoke script `smoke_subscribe.py`.** Asserts `GET /billing/subscription` returns the no-subscription envelope (`{has_subscription: false, ...}`) for a fresh smoke user. Cannot smoke-test full subscribe end-to-end (requires a configured Stripe Price + payment method); that's Phase 1 local with Stripe CLI.

15. **Past-due UX is project-layer.** The chassis exposes `status` via `GET /billing/subscription`; the project decides how to render a past-due banner + "update card" CTA. Customer Portal redirect is 0025.

## Out of scope (deliberate — keeps the ticket atomic)

- **Customer Portal redirect** (`POST /billing/portal-session`) — 0025.
- **Pricing page** (project marketing surface; chassis ships SubscribeForm only).
- **Quota tracking** beyond what the balance ledger does — the chassis already grants balance via `subscription_grant`/`subscription_reset`; "quota per project unit" is a project-layer abstraction on top of balance.
- **Trial periods** — Stripe supports them via Price config; chassis is interval-agnostic but doesn't expose trial UX in v1.
- **Coupons / promo codes** — Stripe supports via Checkout Sessions; chassis Elements flow doesn't expose. Project layer can add later.
- **Annual vs monthly toggle UI** — chassis is interval-agnostic at the schema level; presenting both intervals in the UI is project-layer.
- **In-app subscription change/upgrade flow** — Stripe handles the math via the Portal; chassis doesn't build proration UI.
- **Email notifications for lifecycle events** (subscription started, renewed, payment failed, canceled) — followup ticket; current ticket logs structured events but sends no email.
- **`charge.refunded` / `charge.dispute.*` handlers** — admin tooling; future.
- **`invoice.payment_action_required`** (3DS during off-session retry) — followup; chassis logs the event but sends no email.
- **`customer.subscription.trial_will_end`** — no trial support in v1.
- **Carddroper-specific tiers / prices / `grant_micros` values** — project-layer; chassis ships zero defaults.
- **Backfill for users without `stripe_customer_id`** — already handled by 0023's lazy creation pattern in `/billing/topup`; same pattern in `/billing/subscribe`.
- **`backend/.env.example`, `cloudbuild.yaml`** — no new env vars; nothing to update.
- **payments.md `setup-intent` endpoint addition** — bundled into this ticket as a one-line update; not a separate doc ticket.

## Acceptance

### Phase 0a — backend (backend-builder)

Read `doc/systems/payments.md §Subscription tier contract`, `§Flows items 4–7`, and `§Data model` first. Every contract is spelled out there.

**Repository root:** /Users/johnxing/mini/postapp. Currently on `main`. Do NOT touch `frontend/`.

**1. Alembic migration — `subscriptions` table:**

Schema exactly per `payments.md §Data model`. Index on `user_id` (UNIQUE), `stripe_subscription_id` (UNIQUE).

**2. SQLAlchemy model — `backend/app/models/subscription.py`:**

```python
class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    stripe_subscription_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stripe_price_id: Mapped[str] = mapped_column(String(64))
    tier_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))  # trialing | active | past_due | cancelled | incomplete
    grant_micros: Mapped[int] = mapped_column(BigInteger)
    current_period_start: Mapped[datetime | None]
    current_period_end: Mapped[datetime | None]
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

**3. GrantReason enum extensions — `backend/app/billing/reason.py` (or wherever the enum lives):**

Add `SUBSCRIPTION_GRANT` and `SUBSCRIPTION_RESET`. Keep existing values (`TOPUP`, `SIGNUP_BONUS`, `VERIFY_BONUS`, `DEBIT`, `REFUND`, `ADJUSTMENT`).

**4. Setup-intent endpoint — `POST /billing/setup-intent`:**

- Dep: `Depends(require_verified)`.
- No body. Lazy-creates Stripe Customer if `user.stripe_customer_id is None` (mirrors topup pattern).
- Calls `stripe.SetupIntent.create(customer=user.stripe_customer_id, payment_method_types=["card"], usage="off_session")`.
- Returns `{client_secret: str}`.
- Idempotency: `stripe.SetupIntent.create` accepts `idempotency_key`; use `f"setup:{user.id}:{int(time.time() // 60)}"` (one SI per user per minute).

**5. Subscribe endpoint — `POST /billing/subscribe`:**

- Dep: `Depends(require_verified)`.
- Rate limit: `@limiter.limit(settings.SUBSCRIBE_RATE_LIMIT)`.
- Request body: `SubscribeRequest { price_lookup_key: str, payment_method_id: str }`.
- Implementation:
  1. Resolve Price: `prices = stripe.Price.list(lookup_keys=[body.price_lookup_key], expand=["data.product"])`. If not found, raise 404.
  2. Read `tier_key = price.lookup_key`, `tier_name = price.metadata["tier_name"]`, `grant_micros = int(price.metadata["grant_micros"])`. If any missing, raise 400 with the missing-key name.
  3. Attach PM: `stripe.PaymentMethod.attach(body.payment_method_id, customer=user.stripe_customer_id)` then `stripe.Customer.modify(user.stripe_customer_id, invoice_settings={"default_payment_method": body.payment_method_id})`.
  4. Existing subscription? Query `subscriptions` by `user_id`. If row exists with `status in ('active', 'trialing', 'past_due')`, return 409 `ALREADY_SUBSCRIBED` with current state. (Resubscribe-after-cancel: status `cancelled`; allowed to proceed.)
  5. Create Subscription:
     ```python
     kwargs = {
         "customer": user.stripe_customer_id,
         "items": [{"price": price.id}],
         "default_payment_method": body.payment_method_id,
         "metadata": {"user_id": str(user.id)},
         "expand": ["latest_invoice.payment_intent"],
     }
     if settings.STRIPE_TAX_ENABLED:
         kwargs["automatic_tax"] = {"enabled": True}
     sub = stripe.Subscription.create(**kwargs, idempotency_key=f"subscribe:{user.id}:{body.price_lookup_key}")
     ```
  6. Upsert `subscriptions` row with the returned state (status from `sub.status`, period from `sub.current_period_*`, etc.). Do NOT grant balance here — the webhook handler does that on `customer.subscription.created` (avoids double-grant on retried API calls).
  7. Return `SubscribeResponse { subscription_id: str, status: str, requires_action: bool, client_secret: str | None }`. `requires_action` is true if `sub.status == 'incomplete'` (3DS challenge needed); `client_secret` is `sub.latest_invoice.payment_intent.client_secret` in that case for the frontend to confirm.

**6. Subscription state endpoint — `GET /billing/subscription`:**

- Dep: `Depends(get_current_user)` (authed; not verified-gated).
- Query `subscriptions` by `user_id`.
- Returns `SubscriptionResponse`:
  ```python
  {
      "has_subscription": bool,
      "tier_key": str | None,
      "tier_name": str | None,
      "status": str | None,
      "current_period_end": datetime | None,
      "cancel_at_period_end": bool,
  }
  ```
  When no row: `{has_subscription: false, ...all None/false}`. When row exists with status `cancelled`: `has_subscription: false` (chassis treats cancelled as "no active subscription"; row stays for audit).

**7. Webhook handlers — `backend/app/billing/handlers/subscription.py` (new file):**

Each handler is `async def(event: stripe.Event, db: AsyncSession) -> None` decorated with `@register("event.type")`. Top-line imports: `from app.billing.handlers import register`, `from app.billing.primitives import grant`, `from app.billing.reason import Reason`, plus stripe + AsyncSession types.

- `handle_subscription_created` (`@register("customer.subscription.created")`):
  - Extract `metadata.user_id` from the subscription object; log warning + return on missing.
  - Resolve Price by `id` (not lookup_key — already on the subscription object); read `metadata.grant_micros` + `metadata.tier_name`; log warning + return on missing keys.
  - Upsert `subscriptions` row.
  - Grant `subscription_grant`: `+grant_micros` ledger entry with `stripe_event_id=event.id`. The `stripe_events` idempotency from 0023.2 prevents double-grant on retry.

- `handle_subscription_updated` (`@register("customer.subscription.updated")`):
  - Extract `metadata.user_id`; if subscription row not found, log warning + return (handler ordering: created should fire first; updated arriving first is rare but possible — log and let the next event reconcile).
  - Sync `status`, `current_period_start`, `current_period_end`, `cancel_at_period_end`. Re-read Price metadata in case the user upgraded/downgraded; update `tier_key`, `stripe_price_id`, `grant_micros`. **Do NOT post a ledger entry** — only `customer.subscription.created` and `invoice.paid` post ledger entries.

- `handle_subscription_deleted` (`@register("customer.subscription.deleted")`):
  - Mark `status='cancelled'`. Do NOT clear the row (audit). Do NOT post a refund/revocation ledger entry — already-granted balance is the user's per `payments.md §Cancellation`.

- `handle_invoice_paid` (`@register("invoice.paid")`):
  - Distinguish by `invoice.billing_reason`:
    - `'subscription_create'` → no-op (initial grant fires on `customer.subscription.created`).
    - `'subscription_cycle'` (renewal) → post `subscription_reset`:
      1. Compute `prior_grant_remaining = max(0, get_balance_micros(user) - <committed-since-renewal-grants from this subscription>)`. **Simplification for v1:** the chassis posts a single `subscription_reset` entry of `+grant_micros`. Zeroing the prior-period remainder is described in `payments.md §Reason vocabulary` but adds complexity (requires tracking per-subscription grants through the ledger). Defer the zero-remainder semantics to a follow-up; for v1, `subscription_reset` is a positive grant equivalent to the next period's `grant_micros`, mirroring the flow user-perceives ("$X added each month"). The "zero remaining prior" piece is an accounting nicety; balance increases monotonically across renewals which is closer to what users expect anyway. **Document this simplification in payments.md §Reason vocabulary inline.**
    - other (`'manual'`, `'subscription_threshold'`, `'subscription_update'`, etc.) → log info + no-op (these are off-cycle invoices not relevant to v1 chassis behavior).
  - Update `subscriptions.current_period_*` from the invoice.
  - `stripe_event_id` idempotency from 0023.2 prevents double-grant.

- `handle_invoice_payment_failed` (`@register("invoice.payment_failed")`):
  - Update `subscriptions.status='past_due'`. Log info. Balance stays spendable per `payments.md §Past-due behavior`.

**8. Register handlers — `backend/app/routes/billing.py`:**

Add a single line near the existing `import app.billing.handlers.topup`:

```python
import app.billing.handlers.subscription  # noqa: F401 — registers 5 subscription/invoice events
```

**9. Pydantic schemas — `backend/app/schemas/billing.py` (or wherever they live):**

Add `SubscribeRequest`, `SubscribeResponse`, `SubscriptionResponse`, `SetupIntentResponse`. Field types per items 4–6 above.

**10. Config — `backend/app/config.py`:**

Add `SUBSCRIBE_RATE_LIMIT: str = "5/minute"`. Inline comment: "deliberately un-validated — chassis-tunable rate limit; chassis-contract entry not required (per 0018 audit pattern for rate-limit settings)."

**11. Tests — `backend/tests/test_billing_subscribe.py` (new file):**

Module-level `pytestmark = pytest.mark.skipif(not settings.BILLING_ENABLED, reason=...)` per the 0023.1 Kind-2 isolation pattern.

Coverage:

- `test_setup_intent_requires_verified` — unverified user → 403.
- `test_setup_intent_requires_auth` — no token → 401.
- `test_setup_intent_creates_setup_intent_on_stripe` — verified user → 200 with `client_secret`; verify `stripe.SetupIntent.create` was called with `customer=user.stripe_customer_id`, `payment_method_types=["card"]`, `usage="off_session"`.
- `test_setup_intent_lazy_creates_customer` — user with `stripe_customer_id=None` → SI created; user.stripe_customer_id populated.
- `test_subscribe_requires_verified` — unverified → 403.
- `test_subscribe_rejects_missing_lookup_key` — Price not found → 404.
- `test_subscribe_rejects_missing_grant_micros_metadata` — Price found but `metadata.grant_micros` missing → 400.
- `test_subscribe_rejects_missing_tier_name_metadata` — Price found but `metadata.tier_name` missing → 400.
- `test_subscribe_rejects_already_active_subscription` — user has active sub → 409 `ALREADY_SUBSCRIBED`.
- `test_subscribe_allows_resubscribe_after_cancel` — user has cancelled sub row → succeeds, upserts.
- `test_subscribe_attaches_pm_and_creates_subscription` — verify `stripe.PaymentMethod.attach`, `stripe.Customer.modify`, `stripe.Subscription.create` all called with correct args; verify `subscriptions` row upserted with returned state; verify NO ledger entry yet (webhook does that).
- `test_subscribe_returns_requires_action_when_3ds_needed` — mock `Subscription.create` returning `status='incomplete'` with `latest_invoice.payment_intent.client_secret` → response has `requires_action=true` + `client_secret`.
- `test_get_subscription_returns_no_subscription_for_new_user` — authed user, no row → `{has_subscription: false, ...}`.
- `test_get_subscription_returns_active_subscription` — row exists, status='active' → returns full envelope.
- `test_get_subscription_returns_no_subscription_for_cancelled_row` — row exists, status='cancelled' → `has_subscription: false`.
- `test_get_subscription_requires_auth` — no token → 401.
- `test_handle_subscription_created_grants_initial_period` — fire mock event with metadata.user_id + Price metadata → upserts row + grants `subscription_grant` of `grant_micros`.
- `test_handle_subscription_created_skips_missing_metadata` — event without `metadata.user_id` → handler logs warning + returns; no row, no grant.
- `test_handle_subscription_created_idempotent` — same event posted twice → handler runs once (existing 0023.2 atomic INSERT covers this; verify via `assert_called_once`).
- `test_handle_subscription_updated_syncs_state` — fire updated event with new period_end + cancel_at_period_end=true → row reflects update; no ledger entry.
- `test_handle_subscription_updated_no_op_for_unknown_user` — event for user without a `subscriptions` row → log warning + return.
- `test_handle_subscription_deleted_marks_cancelled` — fire deleted event → row.status='cancelled'; no balance revocation; existing balance unchanged.
- `test_handle_invoice_paid_subscription_create_no_op` — `billing_reason='subscription_create'` → no ledger entry (covered by subscription.created).
- `test_handle_invoice_paid_subscription_cycle_grants` — `billing_reason='subscription_cycle'` → ledger gets `subscription_reset` entry of `+grant_micros`; row's `current_period_*` updated.
- `test_handle_invoice_paid_other_reason_no_op` — `billing_reason='manual'` → no ledger entry, log only.
- `test_handle_invoice_payment_failed_marks_past_due` — fire event → row.status='past_due'; balance unchanged (already-granted balance stays spendable).
- `test_webhook_dispatches_to_subscription_handlers` — fire each of the 5 event types via the live webhook endpoint; each handler invoked exactly once.

**12. Smoke script — `backend/scripts/smoke_subscribe.py`:**

Pattern matches `smoke_billing.py`. Steps:
1. Register fresh smoke user.
2. Authenticate.
3. `GET /billing/subscription` with Authorization.
4. Assert response: `has_subscription == false`, `tier_key is None`, `status is None`.
5. Cleanup: `POST /auth/logout`.

Cannot smoke-test full subscribe (requires Price + payment method); Phase 1 local handles that with Stripe CLI.

**13. Backend API reference — `doc/reference/backend-api.md`:**

Expand the Billing section:
- `POST /billing/setup-intent` — verified. No body. Returns `{client_secret}`.
- `POST /billing/subscribe` — verified + rate-limited (`SUBSCRIBE_RATE_LIMIT`). Body `{price_lookup_key, payment_method_id}`. Returns `{subscription_id, status, requires_action, client_secret?}`.
- `GET /billing/subscription` — authed. Returns `{has_subscription, tier_key, tier_name, status, current_period_end, cancel_at_period_end}`.
- Update webhook entry: lifecycle events are now `payment_intent.succeeded` (topup) + 5 subscription/invoice events (subscribe).

**14. Update `doc/systems/payments.md`:**

Two small additions in the same commit:
- Add `POST /billing/setup-intent` to the §Chassis-exposed HTTP endpoints list.
- In §Reason vocabulary, document the v1 simplification for `subscription_reset` (just adds `+grant_micros` per renewal; doesn't yet zero remaining prior-period grant — described as a future hardening, similar to the cancel-at-period-end-vs-immediate distinction).

**PAUSE-and-report conditions (Phase 0a):**

- The existing `app/billing/handlers/__init__.py` registry pattern doesn't accommodate the 5 new handlers cleanly (extremely unlikely; 0023 designed the dispatch registry for exactly this).
- `payments.md`'s strict `subscription_reset` semantic ("zero remaining prior-period grant + new period grant") is required by an adopter-visible invariant the agent encounters; the v1 simplification per §Design decision #4 isn't sufficient. PAUSE rather than implement the strict version unilaterally.
- `invoice.paid` distinguishing `billing_reason` is materially harder than described (Stripe sends edge cases not captured in this ticket — e.g., proration invoices). PAUSE rather than guess.
- Alembic migration conflicts with existing tables or migration chain.
- Test isolation requires Kind-1/Kind-2 fixtures the agent can't apply via existing patterns from 0023.1.

### Phase 0b — frontend (frontend-builder)

Read ticket §Design decisions #7–#9 + 0023's TopupForm + ProfileMenu Billing-link patterns first.

**Repository root:** /Users/johnxing/mini/postapp. Currently on `main`. Do NOT touch `backend/`.

**1. Subscription hook — `frontend/hooks/useSubscription.ts` (new):**

```ts
export interface SubscriptionResponse {
  has_subscription: boolean;
  tier_key: string | null;
  tier_name: string | null;
  status: string | null;
  current_period_end: string | null;  // ISO-8601
  cancel_at_period_end: boolean;
}

export const SUBSCRIPTION_QUERY_KEY = ["billing", "subscription"] as const;

export function useSubscription(): UseQueryResult<SubscriptionResponse> {
  return useQuery<SubscriptionResponse>({
    queryKey: SUBSCRIPTION_QUERY_KEY,
    queryFn: () => api.get<SubscriptionResponse>("/billing/subscription"),
    staleTime: 30_000,
    retry: false,
  });
}
```

**2. SubscriptionDisplay component — `frontend/components/billing/SubscriptionDisplay.tsx` (new):**

Renders current tier + status + next billing date when subscribed; "no subscription" state otherwise. Follows BalanceDisplay's minimal-render shape.

**3. SubscribeForm component — `frontend/components/billing/SubscribeForm.tsx` (new, client component):**

Per Design decision #7 — two-phase form. Required prop `tiers: Tier[]`.

```ts
export interface Tier {
  lookup_key: string;
  tier_name: string;
  price_display: string;     // e.g., "$9.99/month" — chassis doesn't compute; project supplies
  description?: string;
}

export interface SubscribeFormProps {
  tiers: Tier[];
  onSuccess?: () => void;
}
```

Behavior:
- Render tier cards from `tiers`. If empty, render "No plans available — contact support" placeholder.
- User clicks a tier → moves to Phase B.
- Phase B:
  - POST `/billing/setup-intent` → get `client_secret`.
  - Mount `<Elements stripe={getStripe()} options={{clientSecret, appearance: {theme: 'stripe'}}}><InnerForm ...></Elements>`.
  - Inner form has `<PaymentElement />` + Pay button.
  - On submit: `stripe.confirmSetup({elements, redirect: 'if_required'})`.
  - On success: extract `payment_method_id` from `setupIntent.payment_method`; POST `/billing/subscribe { price_lookup_key, payment_method_id }`.
  - On 3DS-required (subscribe response has `requires_action: true`): use `stripe.confirmPayment({clientSecret: response.client_secret, ...})` flow; loop until cleared.
  - Success: status → `confirmed_polling`; poll `GET /billing/subscription` every 1s up to 10s until `status='active'`; on match render success state, call `onSuccess?.()`.
  - Error states mirror TopupForm (card_error → user message, validation_error → field error, network → generic).

State: `{phase: 'select' | 'fetching_secret' | 'card_entry' | 'submitting' | 'confirmed_polling' | 'active' | 'processing'}`.

**4. Subscribe page — `frontend/app/(app)/app/subscribe/page.tsx` (new):**

```tsx
"use client";

import { SubscribeForm } from "@/components/billing/SubscribeForm";
import { SubscriptionDisplay } from "@/components/billing/SubscriptionDisplay";

export default function SubscribePage() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-2">Subscription</h1>
      <SubscriptionDisplay className="mb-6 text-sm text-gray-600" />
      <SubscribeForm tiers={[]} />
    </div>
  );
}
```

Chassis ships with `tiers={[]}` — adopters override the page in their own fork to pass real tiers. Empty array renders the "no plans available" state per Design decision #9.

**5. ProfileMenu link — `frontend/components/app-shell/ProfileMenu.tsx`:**

Add a "Subscription" link under Settings, after the Billing link, mirroring the 0023 + 0017.1 patterns:

```tsx
<Link
  href="/app/subscribe"
  role="menuitem"
  onClick={() => setOpen(false)}
  className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
>
  Subscription
</Link>
```

DOM order under Settings: Billing → Subscription → Change email → Logout (mirrors the chassis-completion order).

**6. Tests — none automated** (no frontend test harness yet, per 0023 + 0017.1 patterns). Gates:

- `npm run lint` clean.
- `npx tsc --noEmit` clean.
- `npm run build` succeeds.

Manual browser verification is Phase 1.

**PAUSE-and-report conditions (Phase 0b):**

- TopupForm patterns can't be reused for SubscribeForm due to structural differences between Stripe SetupIntent + `confirmSetup` and PaymentIntent + `confirmPayment` flows. PAUSE rather than invent a new form pattern.
- ProfileMenu doesn't have the slot-after-Billing-link pattern the ticket describes (mismatch with 0023's introduced shape). PAUSE rather than refactor.
- `lib/api` wrapper returns response shapes incompatible with the spec'd `SubscribeResponse` / `SubscriptionResponse`. PAUSE.
- 3DS challenge handling within `confirmSetup` requires a flow not described here (Stripe's challenge modal interaction differs between SetupIntent and PaymentIntent paths in some scenarios). PAUSE rather than guess.
- A backend issue is discovered during integration (the `/billing/setup-intent` endpoint returns a different shape than spec'd, etc.). Backend is locked to Phase 0a's contract; PAUSE.

### Phase 1 — user local test

**Prep (one-time):**

1. Stripe Test Dashboard → Products → create at least one Product (e.g., "Starter") with at least one recurring Price (e.g., "$9.99/month").
2. On the Price, set:
   - **Lookup key** (top-level field, not metadata): `starter_monthly`.
   - **Metadata:** `tier_name = "Starter"`, `grant_micros = "10000000"` (= $10/period).
3. Note the price's `id` (`price_...`) for sanity-checking.

**Smoke flow:**

1. `BILLING_ENABLED=true` in `backend/.env` + Stripe test-mode keys (reuse 0023's setup).
2. `stripe listen --project-name carddroper --forward-to localhost:8000/billing/webhook --events customer.subscription.created,customer.subscription.updated,customer.subscription.deleted,invoice.paid,invoice.payment_failed,payment_intent.succeeded`.
3. `docker-compose up -d --build`.
4. Log in → ProfileMenu → Subscription → land on `/app/subscribe`. Empty tiers state ("no plans available").
5. Stop frontend; modify the page locally to pass real `tiers` (one or two from Stripe Dashboard) — quick ad-hoc test, do NOT commit:
   ```tsx
   <SubscribeForm tiers={[{
     lookup_key: "starter_monthly",
     tier_name: "Starter",
     price_display: "$9.99/month",
     description: "10 dollars in monthly credit",
   }]} />
   ```
6. Reload. Click "Starter" tier. Card form appears.
7. Use test card `4242 4242 4242 4242`, future expiry, any CVC, any ZIP. Click Pay.
8. Expected:
   - Stripe CLI shows `customer.subscription.created` (200), `invoice.paid` with `billing_reason=subscription_create` (200; chassis no-ops on this), and possibly `customer.subscription.updated` (200).
   - `/app/subscribe` shows "Active subscription: Starter, next billing on <date>".
   - `/app/billing` balance shows `$10.00` (the `grant_micros` from Price metadata).
   - DB: `SELECT * FROM subscriptions WHERE user_id=<id>;` shows row with `status='active'`, `tier_key='starter_monthly'`, `grant_micros=10000000`.
   - DB: `SELECT reason, amount_micros / 1000000.0 AS dollars FROM balance_ledger WHERE user_id=<id>;` shows `subscription_grant` row with `10.000000`.

**3DS test:** test card `4000 0027 6000 3184` triggers a 3DS challenge during `confirmSetup`. Verify the flow handles it (Stripe Elements pops a modal; user clicks "Complete authentication"; flow continues).

**Decline test:** test card `4000 0000 0000 0002` on the SetupIntent → see inline error; form stays mounted; retry with `4242` succeeds.

**Past-due test:** Stripe Dashboard → Customer → Subscription → "Update payment method" → use test card `4000 0000 0000 0341` (succeeds initially, fails on next renewal). Wait for renewal (or use Stripe CLI `stripe trigger invoice.payment_failed`). Verify chassis marks `subscriptions.status='past_due'`; balance unchanged.

**Cancellation test:** Stripe Dashboard → Subscription → Cancel (at period end). Verify `customer.subscription.updated` fires with `cancel_at_period_end=true`; chassis records flag. Don't wait for period end (would take a month); use Stripe CLI `stripe trigger customer.subscription.deleted` to simulate. Verify `subscriptions.status='cancelled'`; balance unchanged.

**Regression checks:**

- `/app/billing` topup still works (0023 unchanged).
- `/app/change-email` still works (0017.1 unchanged).
- All smoke scripts still green.
- Two-state pytest still green.

### Phase 2 — user staging

1. Stripe Test Dashboard for staging — create Products + Prices with `metadata.grant_micros` + `metadata.tier_name` + `lookup_key`.
2. Stripe Dashboard → Webhooks → existing endpoint at `https://api.staging.carddroper.com/billing/webhook` → add the 5 new events: `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
3. Push `main`. Cloud Build redeploys.
4. Smoke battery (now 6 scripts):
   ```bash
   cd backend
   .venv/bin/python scripts/smoke_healthz.py
   .venv/bin/python scripts/smoke_auth.py --expected-cookie-domain .staging.carddroper.com
   .venv/bin/python scripts/smoke_cors.py
   .venv/bin/python scripts/smoke_verify_email.py
   .venv/bin/python scripts/smoke_billing.py
   .venv/bin/python scripts/smoke_subscribe.py
   ```
5. Manual subscribe flow on staging — same as Phase 1 step 6–8 but against `https://staging.carddroper.com`. Project layer (carddroper-body) is the only place to put real `tiers`; until that ticket lands, the staging UI shows "no plans available." That's expected — chassis is verified by the API + webhook, not by full E2E UI.

## Verification

**Automated (backend-builder Phase 0a report):**

- `.venv/bin/pytest` under `BILLING_ENABLED=true` and `=false` — both green; new tests in `test_billing_subscribe.py` pass under enabled, skip under disabled.
- `ruff check` + `ruff format --check` clean.
- Paste final definitions of: subscribe endpoint, setup-intent endpoint, GET subscription endpoint, the 5 webhook handlers, the new GrantReason values, the Subscription model.
- Confirm `smoke_subscribe.py` runs against local dev and exits 0.
- Pytest + ruff summary lines.

**Automated (frontend-builder Phase 0b report):**

- `npm run lint` / `npx tsc --noEmit` / `npm run build` all clean.
- Paste final: `useSubscription.ts`, `SubscriptionDisplay.tsx`, `SubscribeForm.tsx` (full — this is the complex one), `app/(app)/app/subscribe/page.tsx`, ProfileMenu Subscription-link diff.
- Confirm `package.json` unchanged (no new deps; reuse Stripe libraries from 0023).
- Confirm untouched: `(app)/layout.tsx`, `(marketing)/layout.tsx`, `(auth)/layout.tsx`, `lib/api.ts`, `context/auth.tsx`, BalanceDisplay/TopupForm from 0023.

**Functional (user, Phase 1):**

- Steps 1–8 + 3DS + decline + past-due + cancellation + regression all pass locally.

**Staging (user, Phase 2):**

- Smoke battery green (6 scripts).
- New webhook events verified in Stripe Dashboard's Event Log on staging endpoint.

## Chassis implications

After 0024 closes:

- The payments chassis has both halves complete: PAYG topup (0023) + recurring subscriptions (0024). Adopters get both surfaces by setting `BILLING_ENABLED=true` + Stripe keys + populating Stripe Prices with `metadata.grant_micros` + `metadata.tier_name`.
- `subscriptions` table + 5 lifecycle handlers + `subscription_grant`/`subscription_reset` reasons all chassis-owned; project layer adds tiers in Stripe Dashboard, no code change.
- Dispatch registry (proven by 0023) holds 6 event handlers cleanly. 0025 (Customer Portal `POST /billing/portal-session`) is the last billing-chassis ticket; it adds zero new webhook handlers.
- Per `payments.md §Cancellation`, in-app cancellation UI is intentionally NOT in the chassis — Customer Portal handles it (lands in 0025). 0024 records `cancel_at_period_end` from Stripe-fired updates.

No new `chassis-contract.md` entries needed — 0024 reuses the existing Stripe + boot-time invariants from 0021/0023/0018.

## Report

Backend-builder (Phase 0a):

- Files modified + one-line what-changed each.
- Pasted artifacts per §Verification.
- Pytest + ruff summary lines.
- Any deviation from the brief, with reasoning.
- Any callsite/handler/migration found NOT in this brief (gap signal).

Frontend-builder (Phase 0b):

- Files modified + one-line what-changed each.
- Pasted artifacts per §Verification.
- Lint / tsc / build summary lines.
- Any deviation.

Orchestrator (on close):

- User Phase 1 outcomes (subscribe, 3DS, decline, past-due, cancellation, regressions).
- User Phase 2 staging outcomes (smoke battery, webhook event verification).

## Resolution

*(filled in by orchestrator after backend-builder + frontend-builder + user confirms Phase 1 + Phase 2 pass)*
