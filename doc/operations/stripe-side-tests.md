# Stripe-side test battery

Tests that exercise the chassis with **real Stripe events**, not unit-test mocks. These complement (don't replace) the local pytest suite — unit tests verify "this handler does what it says"; Stripe-side tests verify "this handler works against real Stripe-shaped basil-API event payloads delivered through real webhook flow".

The 0024.x billing arc surfaced **two bug classes that unit tests structurally cannot catch**:

1. **Stripe API version drift** — basil moved fields (`current_period_*`, `invoice.subscription`); mocks reflected the OLD shape; tests passed; production silently failed. (Tickets 0024.4, 0024.12.)
2. **Cross-writer composition** — multiple writers to the same column under different real-event-ordering scenarios. Mocks usually cover one writer at a time. (Tickets 0024.5, 0024.7, 0024.11, 0024.13.)

Both classes require running the chassis against actual Stripe-emitted events.

## When to run

The Stripe-side battery is **required** for any ticket that:

- Modifies a Stripe webhook handler (`app/billing/handlers/*`)
- Modifies a Stripe API call site (`stripe.Subscription.create`, `stripe.SetupIntent.create`, etc.)
- Adds or changes idempotency keys
- Changes how the chassis extracts fields from Stripe objects (e.g., `app/billing/stripe_extractors.py`)

It is **recommended** for any ticket touching `app/routes/billing.py` or `app/billing/primitives.py` even if no handler is modified — the cross-writer audit (audit-template Q3.5) often surfaces hidden interactions.

## Tier A — Stripe CLI synthetic event triggers

Cheapest test. Fires real basil-shape events at the local backend through `stripe listen`.

### A1. Force a renewal cycle without waiting 30 days (LIMITED — see note)

```bash
stripe trigger invoice.paid
```

**What this does:** Stripe creates fresh fixture objects (customer, subscription, invoice) and fires `invoice.paid` against them.

**Limit:** `stripe trigger` does **not** allow overriding the subscription ID or `billing_reason` (Stripe API rejects `--override invoice:billing_reason=subscription_cycle` with `parameter_unknown`). The triggered event has `billing_reason='manual'` (default) and references its own fixture subscription, NOT a subscription already in your DB.

**What it actually verifies:**
- The chassis correctly identifies non-cycle billing_reasons and no-ops (the dispatch logic works)
- `extract_invoice_subscription_id` from `stripe_extractors.py` runs without error against real basil shape
- The webhook delivery + signature verification + dedup work end-to-end

**What it does NOT verify:** the `subscription_cycle` branch's grant logic. For that, use Tier B test clocks.

**Verify after running:**
```sql
SELECT id, event_type, processed_at FROM stripe_events ORDER BY processed_at DESC LIMIT 3;
-- Expect: invoice.paid event present
```
```bash
docker-compose logs --tail=20 backend | grep "handle_invoice_paid"
-- Expect: "billing_reason='manual' — no-op (not a renewal cycle)"
```

### A2. `customer.subscription.updated` for a real existing subscription

Stripe trigger can't reference a specific subscription, but you can **mutate a real subscription via the Stripe API** to fire a real `.updated` event referencing it. Cleanest: toggle `cancel_at_period_end`.

```bash
# Forward
stripe subscriptions update sub_<your-active-sub-id> --cancel-at-period-end=true
# Verify
docker-compose exec db psql -U carddroper -d carddroper -c \
  "SELECT cancel_at_period_end, grant_micros, current_period_start FROM subscriptions WHERE stripe_subscription_id='sub_<id>';"
# Revert
stripe subscriptions update sub_<your-active-sub-id> --cancel-at-period-end=false
```

**What it verifies:**
- `handle_subscription_updated` runs against a real basil-shape `customer.subscription.updated` event
- Path B preservation: `grant_micros` and `current_period_*` UNCHANGED (only `cancel_at_period_end` flips)
- This is the canonical regression test for tickets 0024.5 + 0024.7

**Past relevance:** would have caught 0024.5 and 0024.7 if run before those tickets shipped.

### A3. `customer.subscription.deleted` (cancel via Stripe Dashboard or API)

```bash
stripe subscriptions cancel sub_<id>
```

**Verify:**
- `subscriptions.status='cancelled'`
- `grant_micros` and period fields UNCHANGED (Path B preservation across cancel handler)
- `balance_ledger` UNCHANGED (cancel does not revoke prior grants per `payments.md` §Cancellation)

**Note:** this is destructive — only run on a sub you don't need. Useful as part of pre-push smoke for new subs created specifically for testing.

### A4. `invoice.payment_failed` synthetic

```bash
stripe trigger invoice.payment_failed
```

**What this verifies:** the handler runs against a real basil-shape `invoice.payment_failed` event without errors. The fixture subscription doesn't exist in your DB so the row lookup will fail gracefully (warning logged, no-op).

**For real-sub testing:** would require creating a sub that fails dunning, which is hard to engineer outside test clocks. Defer to Tier B.

## Tier B — Stripe Test Clocks (renewal verification)

The only way to test the `subscription_cycle` branch end-to-end. **One-time setup; reusable across all future chassis tickets.**

### Setup (do this once)

```bash
# 1. Create a test clock at "now"
stripe test_helpers test_clocks create --frozen-time $(date -u +%s)
# → returns clock_<id>; record this

# 2. Create a customer attached to the test clock
stripe customers create --test-clock=clock_<id> \
  --email=clock-test@carddroper.example
# → returns cus_<id>; record this

# 3. Register an app user (via the chassis registration flow) whose
#    stripe_customer_id matches cus_<id>. There are two paths:
#
#    Option a (simpler): register a user normally; then UPDATE the user's
#    stripe_customer_id in the DB to match cus_<id>. Requires that the
#    app user's account has not yet started a subscribe flow (which would
#    have lazily created a different stripe_customer_id).
#
#    Option b (more invasive): modify the chassis Customer creation step
#    to use this specific customer_id during the subscribe flow. Don't
#    commit this change.

# 4. Subscribe via the chassis as that user (with flag=true to test grants).
#    This creates a Subscription on the test clock.

# 5. Record the resulting sub_<id>.
```

The test customer/clock pair becomes a permanent test fixture. Document the IDs in a gitignored file (`backend/.test-clock-fixture.local`) so future runs can use them.

```bash
# 6. Save the IDs in backend/.test-clock-fixture.local (template: .test-clock-fixture.local.example).
cp backend/.test-clock-fixture.local.example backend/.test-clock-fixture.local
# Edit the file and fill in customer_id, clock_id, user_id, subscription_id.

# 7. Run python backend/scripts/test_renewal.py to verify the renewal cycle behavior.
#    The script will advance the test clock by 31 days, wait for webhooks, capture
#    pre/post DB state, and assert the chassis renewal behavior is correct.
#    It is the canonical Tier B1 test for any future ticket touching the
#    handle_invoice_paid subscription_cycle branch.
python backend/scripts/test_renewal.py
```

### B1. Advance through one renewal

```bash
# Advance the clock by 31 days
stripe test_helpers test_clocks advance \
  --frozen-time $(date -u -v +31d +%s) clock_<id>

# Wait ~10 seconds for events to process
sleep 10
```

**Verify:**
```sql
SELECT current_period_start, current_period_end, grant_micros, updated_at
FROM subscriptions WHERE stripe_subscription_id='sub_<id>';
```
- `current_period_start` advanced to old `current_period_end`
- `current_period_end` is +30 days from new start
- `grant_micros` unchanged (no tier change)

```sql
SELECT id, amount_micros, reason, stripe_event_id, created_at
FROM balance_ledger WHERE user_id=<id> ORDER BY id DESC LIMIT 3;
```
- New `subscription_reset` ledger entry with `amount_micros = grant_micros` (when flag=true)
- No new entry (when flag=false)

```bash
docker-compose logs --tail=50 backend | grep "subscription_cycle\|subscription_reset"
```
- Confirms the cycle branch fired

**This is the highest-coverage Stripe-side test.** It exercises:
- Real basil-shape `invoice.paid` with `billing_reason=subscription_cycle` (vs only `manual` from `stripe trigger`)
- Path B period writes (0024.5)
- Path B grant_micros writes (0024.7) when tier is unchanged
- Grant coupling to invoice.paid (0024.11)
- Basil `invoice.subscription` extraction (0024.12)

### B2. Tier change mid-subscription + renewal

Between subscribing and advancing the clock, change the subscription's price via Dashboard or API:

```bash
# Get the subscription item ID
stripe subscriptions retrieve sub_<id> | grep -A 5 '"items"' | grep '"id"'

# Update to a different Price (assumes you have multiple tiers in Stripe)
stripe subscription_items update si_<id> --price=price_<new-tier>

# Then advance the clock as in B1
```

**Verifies:**
- `customer.subscription.updated` writes new `tier_key`/`tier_name`/`stripe_price_id` (per Path B)
- `customer.subscription.updated` does NOT touch `grant_micros` or periods
- Renewal `invoice.paid` (subscription_cycle) updates `grant_micros` from new Price metadata when flag=true
- `subscription_reset` ledger entry uses the new tier's grant amount

## Tier C — Stripe Dashboard manipulations

Manual UI-driven tests. Slowest but most realistic for verifying webhook delivery under production-like conditions.

### C1. Cancel via Stripe Dashboard

Dashboard → Customers → \[test customer\] → Subscriptions → \[active sub\] → "Cancel subscription".

**Verify:** same as Tier A3 (status='cancelled'; Path B preservation).

### C2. Update default payment method via Dashboard

Dashboard → Customers → \[customer\] → "Update default payment method".

**Verify:** does the chassis observe this? Currently NO — `customer.updated` is not a subscribed event in our endpoint configuration. Worth knowing for ticket 0025 (Customer Portal) where users update PMs via Stripe-hosted UI.

### C3. Mark invoice uncollectible / void

Dashboard → Invoices → \[invoice\] → "Mark as uncollectible" or "Void".

**Verify:** does the chassis handle these states? Currently NO — these events aren't subscribed. Documenting the gap; out of v1 scope.

## Test ordering for a new chassis ticket

For any chassis ticket touching webhook handlers or Stripe API calls, the Phase 1 manual smoke should include:

1. **Local final smoke** with the success and decline cards (matrix of flag states)
2. **Tier A2** — toggle `cancel_at_period_end` on an existing sub; verify Path B preservation
3. **Tier B1** — if the cycle branch was touched, advance the test clock; verify renewal grant
4. (Optional) **Tier B2** — if tier-change logic was touched; verify mid-sub upgrade

Tickets that explicitly request only Tier A2 + B1 are sufficient for most chassis correctness work. Tier C is heavier and deferred to ticket 0025 onward.

## Limitations to be aware of

- `stripe trigger` cannot override fixture object IDs or `billing_reason`. Tier A is fixture-only; for real-sub testing use API mutations or test clocks.
- Test clocks must be attached at customer creation time. You cannot retroactively attach a clock to an existing customer or subscription. Plan ahead.
- Test clocks only advance time; they don't simulate failures. For failure-path testing, use specific Stripe test cards in the subscribe flow, not test clocks.
- Stripe events delivered via test clock advancement may take 5–15 seconds to arrive at your local backend. `sleep 10` after advancing is a reasonable wait.

## Origin

Ticket 0024.x billing chassis arc retrospective (2026-04-29). The arc surfaced multiple Stripe API drift bugs (0024.4, 0024.12) and cross-writer composition bugs (0024.5, 0024.7, 0024.11, 0024.13) that mocked unit tests structurally cannot catch. This battery is the durable lesson — every future ticket touching Stripe interaction starts here.
