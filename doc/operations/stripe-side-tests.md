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
#
#    NOTE: docker-compose forwards Postgres on host port 5433 (not 5432, to avoid
#    clashing with any host-installed Postgres). The script reads DATABASE_URL via
#    pydantic-settings; if backend/.env has a host-Postgres URL on port 5432 (a
#    common dev setup), override it on the command line to point at the
#    docker-compose DB:
DATABASE_URL='postgresql+asyncpg://carddroper:carddroper@localhost:5433/carddroper' \
  .venv/bin/python backend/scripts/test_renewal.py
```

The same override pattern works for the optional `flag=false` re-run: stop the backend, set `BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=false` in the script's environment (or rely on app.config picking it up from a modified `.env`), and run again. The script will advance the clock another 31 days and assert no new `subscription_reset` ledger entry was posted.

**Why running from host (not inside the container):** the chassis Dockerfile deliberately excludes `scripts/` from the production image — `test_renewal.py` is dev tooling and uses Stripe Test Clocks, which are never appropriate for production. The chassis convention is: `app/` runs in containers; `scripts/` runs from host (or CI runners) against deployed/local services.

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

### B3. Renewal failure verification

Tests the `handle_invoice_payment_failed` path: subscription's renewal charge fails, chassis sets status to `past_due`, period and grant_micros are preserved (Path B).

**Flag:** `--simulate-decline`

**Test PM used:** `pm_card_chargeCustomerFail` — a Stripe pre-built test PaymentMethod that attaches to a customer successfully but fails on charge. This is the canonical token for renewal-failure simulation in test mode (any Stripe SDK version).

**Expected outcome:**
- `status` flips `active → past_due` after the failed charge.
- `current_period_start` UNCHANGED (period does not advance on failure).
- `current_period_end` UNCHANGED (period does not advance on failure).
- `grant_micros` UNCHANGED (Path B: no tier or grant changes on failure).
- No new `subscription_reset` ledger entry posted.
- No new `subscription_grant` ledger entry posted.

**`--restore-active` flag (default True):** After assertions pass, the script attempts to pay the failed invoice via `stripe.Invoice.pay(failed_invoice_id)` to bring the subscription back to `active` for clean fixture re-use. This is best-effort — if the original PM was detached during the test, the pay call logs a warning and continues. Use `--no-restore-active` to intentionally leave the subscription in `past_due` state, e.g., as a starting fixture for the 0025 Customer Portal recovery-flow test.

**PM restoration:** The script reads the subscription's `default_payment_method` before swapping it to `pm_card_chargeCustomerFail`, and restores it in a `finally` block — always runs even if assertions fail mid-way. If the original PM was `None` (subscription had no default PM), the script logs a warning.

**Sample invocation:**

```bash
# Run from the backend/ directory
DATABASE_URL='postgresql+asyncpg://carddroper:carddroper@localhost:5433/carddroper' \
  .venv/bin/python backend/scripts/test_renewal.py --simulate-decline
```

**Leave sub in past_due for 0025 testing:**

```bash
DATABASE_URL='postgresql+asyncpg://carddroper:carddroper@localhost:5433/carddroper' \
  .venv/bin/python backend/scripts/test_renewal.py --simulate-decline --no-restore-active
```

**Verify after running:**

```sql
-- Expect status='past_due'; period fields and grant_micros unchanged vs pre-run
SELECT stripe_subscription_id, status, current_period_start, current_period_end, grant_micros
FROM subscriptions WHERE stripe_subscription_id='sub_<id>';
```

```bash
docker-compose logs --tail=50 backend | grep "invoice.payment_failed\|past_due"
-- Expect: handle_invoice_payment_failed ran; status set to past_due
```

**What this test verifies (0024.15):**
- `handle_invoice_payment_failed` correctly transitions status to `past_due`.
- Path B preservation under failure: period + grant_micros NOT overwritten.
- No phantom ledger writes on failed payment.
- `handle_subscription_updated` (fired by the PM swap + status change) does not touch period or grant_micros.
- PM restoration is always attempted (finally block).

**Origin:** ticket 0024.15. Counterpart to B1 (success path). These two together give the chassis full real-Stripe-event coverage across both renewal outcomes.

### B4. Fixture recovery — `--recover-fixture`

When a test rig fixture lands in a broken state (typically: `--simulate-decline --no-restore-active` left the sub in `past_due` with the fail PM as default; OR rapid clock advances pushed the sub all the way to `canceled`), use `--recover-fixture` to restore it without manual Stripe Dashboard intervention.

**Flag:** `--recover-fixture` (does NOT advance the clock; pure recovery)

**Required fixture field:** `original_payment_method_id` in `backend/.test-clock-fixture.local`. Find via `stripe subscriptions retrieve <sub_id> | grep default_payment_method`.

**Two recovery paths the script handles:**

1. **`past_due` recovery** — sub still exists, PM is just wrong. Script does:
   - Set `default_payment_method` back to the fixture's `original_payment_method_id` (with retry on Stripe's "clock advancement underway" rejection — up to 5 attempts, 5s apart)
   - Pay the latest unpaid invoice via `stripe.Invoice.pay()` (uses the now-working PM)
   - Verify status returns to `active`

2. **`canceled` (or `incomplete_expired`) recovery** — sub is terminal; Stripe blocks all modifications. Script does:
   - Read price_id from the canceled sub's items
   - Re-attach the original PM to the customer (best-effort; "already attached" is OK)
   - Create a NEW subscription on the same customer + clock with the original PM and `metadata.user_id`
   - Print the new `subscription_id` and ask the user to update the fixture file (the script does not auto-modify the gitignored `.local` file)

**Sample invocation:**

```bash
DATABASE_URL='postgresql+asyncpg://carddroper:carddroper@localhost:5433/carddroper' \
  .venv/bin/python backend/scripts/test_renewal.py --recover-fixture
```

**After the canceled-recovery path:** edit `backend/.test-clock-fixture.local` to set `subscription_id` to the new sub_id printed by the script. customer_id, clock_id, user_id, original_payment_method_id stay the same.

**Origin:** ticket 0024.15 follow-up. Two real-world scenarios surfaced in Phase 1 manual smoke. Codifies the recovery commands so adopters of the chassis can repair their test rigs without a Stripe Dashboard side-quest.

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
