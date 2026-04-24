---
id: 0023
title: PAYG topup (chassis) — /billing/topup + /billing/balance + payment_intent.succeeded handler + Stripe Elements TopupForm
status: open
priority: medium (first user-facing billing surface; completes the "user pays money → balance increases" loop end-to-end; chassis primitive, not carddroper-specific)
found_by: PLAN.md §10.6 Stripe-layer roadmap; sequenced second after 0022 (app-shell refactor complete) so the Billing link slots into the existing ProfileMenu Settings section without layout churn. Pre-scope decisions locked in during the 2026-04-23 ultrathink sessions on Qs 8/9/10 (presets+free-form, idempotency now, no forced balance placement).
---

## Context

First user-facing billing surface built on the 0021 foundation. Adds the complete PAYG topup loop:

1. User clicks `Billing` in the ProfileMenu's Settings section (slotted in by this ticket).
2. Lands on `/app/billing` — sees current balance + TopupForm with preset buttons ($5/$20/$50 default) and free-form input.
3. Selects amount → backend creates a Stripe PaymentIntent (metadata.user_id set; idempotency key scoped to the minute) → returns `client_secret`.
4. Frontend mounts Stripe `PaymentElement` with that secret. User enters card. Clicks "Pay."
5. `stripe.confirmPayment()` succeeds. Frontend polls `GET /billing/balance` every 1s (up to 10s) until the balance reflects the topup.
6. Meanwhile, Stripe fires `payment_intent.succeeded` webhook → our handler (registered via the new dispatch registry) extracts `metadata.user_id`, converts cents → micros, calls `billing.grant(user_id, amount_micros, Reason.TOPUP, stripe_event_id=event.id)`.
7. Ledger entry lands. Next poll from the frontend sees the new balance. Success UI renders.

**Chassis framing.** Project-agnostic throughout. Preset amounts are chassis defaults that any project can override via a single prop. No carddroper-specific copy, no carddroper-specific debit logic (that's project-layer; 0022.x / carddroper-body tickets will call `billing.debit()` from their own handlers).

Full chassis design is in `doc/systems/payments.md` §Flows (items 2 "Topup" + 6 payment_intent.succeeded). This ticket implements those flows verbatim.

## Design decisions (pre-committed)

All major shape decisions were locked in during the 2026-04-23 planning session. What's pre-committed for the ticket:

1. **Stripe `PaymentElement`, not `CardElement`.** Stripe's modern recommended element; auto-configures based on Stripe Dashboard settings (card + Apple Pay + Google Pay + Link with zero extra integration). Future-proof. Less code.

2. **Dispatch registry pattern for webhook handlers.** New module `backend/app/billing/handlers/` with:
   - `__init__.py` exposing `EVENT_HANDLERS: dict[str, EventHandler]` and a `register(event_type)` decorator.
   - `topup.py` with `@register("payment_intent.succeeded")` decorator on the handler function.
   - `routes/billing.py` webhook endpoint updated to look up `EVENT_HANDLERS.get(event.type)` and dispatch; unregistered event types continue to log "Unhandled" + return 200 (existing behavior).
   - Clean extension point for 0024 subscription handlers + 0025 refund handlers.

3. **Preset buttons AND free-form input (Starbucks model).** `TopupForm` renders both:
   - Preset buttons from `presetAmounts` prop (default `[500_000, 2_000_000, 5_000_000]` = $5 / $20 / $50).
   - A custom amount input (numeric, $ symbol) that activates for amounts outside the presets.
   - Component validates the final amount is in `[minAmountMicros, maxAmountMicros]` range (props default to chassis defaults $0.50 / $500).
   - Overridable presets/min/max via props. Carddroper-body ticket later passes its own values. Chassis ships with sensible defaults.

4. **Backend PaymentIntent idempotency key.** `f"topup:{user.id}:{amount_micros}:{int(time.time() // 60)}"` — same user + same amount in the same minute = same Stripe PaymentIntent (Stripe dedups). Prevents duplicate PIs from double-click / timeout-retry. Minute-window keeps legitimate-retry latency bounded.

5. **Post-confirmation balance polling.** After `stripe.confirmPayment()` resolves `success`, the TopupForm:
   - Immediately invalidates `['billing', 'balance']` query.
   - Polls `GET /billing/balance` every 1000ms for up to 10 seconds OR until balance reflects the topup (`new_balance >= prior_balance + amount_micros`).
   - On poll success: render "✓ Balance updated" state; stop polling.
   - On 10s timeout with no balance change: render "Payment processing — your balance will update shortly" state; stop polling. User can refresh later.
   - Rationale: bridges the ~1-2s race between client-side PI confirmation and our webhook handler writing the ledger entry. Chassis-deterministic UX.

6. **Explicit error handling in TopupForm.** Stripe's `confirmPayment()` returns a structured error object on failure:
   - `card_error`: card declined, insufficient funds, expired, etc. → show Stripe's user-facing `error.message`.
   - `validation_error`: form field issue (Stripe catches most before submit).
   - `invalid_request_error`: rare — programmer error, show generic "Something went wrong, please try again."
   - Network failures during confirmPayment → generic "Connection error, please try again."
   - All error states keep the form mounted so the user can retry without re-entering amount. Stripe's PaymentElement preserves card input across retries.

7. **Topup endpoint gates on `require_verified`.** Unverified users cannot topup (matches payments.md §Flows #2 step 1 and §User states).

8. **Balance endpoint is authed-only (not verified-gated).** Even unverified users can query their balance; returns `$0.00` if they have no ledger entries. Avoids 403 handling in the UI for a common path.

9. **Lazy Customer creation inside /billing/topup.** If `user.stripe_customer_id is None` (user registered before `BILLING_ENABLED=true`), topup endpoint calls `billing.create_customer(user, db)` to create it in Stripe + stores the id on the user row before creating the PaymentIntent. Same idempotency pattern as the register hook (`idempotency_key=f"register:{user.id}"`). Backfill-free: the chassis lazily creates Customers as they're first needed.

10. **`BalanceDisplay` + `useBalance` chassis primitives; NO forced placement.** Components are built and usable anywhere; 0023 uses them on `/app/billing`. Chassis does NOT modify `AppSidebar` or `AppHeader` to include a persistent balance pill. Projects decide placement (header, sidebar, dedicated page) in their own layer.

11. **`Billing` link in ProfileMenu — one-line addition.** 0022 left the `Settings` section as a label with no children. 0023 adds a single `<Link>` to `/app/billing` under the Settings label. Same menu interactions (click-outside close, Escape close) inherited from 0022's implementation.

12. **`NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` frontend build-time env var.** Same pattern as `NEXT_PUBLIC_API_BASE_URL`. Updates to `frontend/.env.example`, `docker-compose.yml` (build args), `cloudbuild.yaml` (build-arg flag). Frontend throws clearly at Elements-init time if missing (no silent failure).

13. **Topup rate limit: 10/minute per IP** (new `TOPUP_RATE_LIMIT = "10/minute"` in Settings, configurable). Matches existing refresh/logout rate limits. Prevents PI-creation spam.

14. **New smoke script `smoke_billing.py`.** Asserts authed `GET /billing/balance` returns valid envelope `{balance_micros: 0, formatted: "$0.00"}` for a fresh smoke user. Cannot smoke-test topup (requires card + Stripe test-mode infrastructure); that's Phase 1 local with Stripe CLI.

15. **No project layer decisions baked in.** This is a recurring discipline from the chassis reframe. All amounts, copy, and UX opinions stay chassis-generic.

## Out of scope (deliberate — keeps the ticket atomic)

- `customer.subscription.*` and `invoice.*` webhook handlers (0024 subscribe).
- `charge.refunded` webhook handler (admin tooling; future).
- `charge.dispute.*` chargeback handlers (policy decisions deferred).
- `POST /billing/portal-session` (0025 Customer Portal).
- Transaction history UI (deferred — users see balance, not ledger entries).
- Any changes to `(marketing)/layout.tsx`, `(auth)/layout.tsx`, auth flow, `lib/api.ts` interceptor logic, `context/auth.tsx`.
- Admin refund primitive (`billing.refund()`) — wait until admin tooling ticket.
- Automatic top-up / auto-reload when balance low (product-layer feature).
- Apple Pay / Google Pay button UX tuning beyond Stripe's auto-config.
- Mobile polish on narrow screens below 375px (popover overflow noted in 0022; carry forward).
- Carddroper-specific preset amounts / action debit costs (project-layer ticket after chassis is complete).
- Any backfill script for pre-existing users without `stripe_customer_id` (lazy creation inside /billing/topup covers the forward path).

## Acceptance

### Phase 0a — backend (backend-builder)

Read `doc/systems/payments.md` §Flows items 2 + 6 first. Every contract (request/response shape, reason vocabulary, primitive signatures, webhook idempotency) is spelled out there. This ticket implements those flows.

**Repository root:** /Users/johnxing/mini/postapp. Currently on `main`. Do NOT touch `frontend/`.

**1. Dispatch registry (`backend/app/billing/handlers/`) — new directory:**

- `__init__.py`:
  ```python
  from typing import Awaitable, Callable
  import stripe
  from sqlalchemy.ext.asyncio import AsyncSession

  EventHandler = Callable[[stripe.Event, AsyncSession], Awaitable[None]]
  EVENT_HANDLERS: dict[str, EventHandler] = {}

  def register(event_type: str) -> Callable[[EventHandler], EventHandler]:
      def decorator(fn: EventHandler) -> EventHandler:
          EVENT_HANDLERS[event_type] = fn
          return fn
      return decorator

  # Import handler modules to register their decorators.
  from app.billing.handlers import topup  # noqa: F401, E402
  ```

- `topup.py`:
  - `@register("payment_intent.succeeded")` on `async def handle_payment_intent_succeeded(event, db)`.
  - Extract `metadata.user_id`; log warning + return on missing/invalid.
  - Extract `amount` (cents); log warning + return if missing/≤0.
  - Convert cents → micros (×10_000).
  - Call `await grant(user_id, amount_micros, Reason.TOPUP, db=db, stripe_event_id=event.id)`.
  - `stripe_events` idempotency (existing in routes/billing.py) prevents double-grant on webhook replay.

**2. Webhook route dispatch (`backend/app/routes/billing.py`):**

Inside the existing signature-verification + idempotency-check block, replace the "Unhandled Stripe event type" warning with:

```python
from app.billing.handlers import EVENT_HANDLERS

# ...inside the webhook handler, after stripe_events duplicate check...
handler = EVENT_HANDLERS.get(event.type)
if handler:
    await handler(event, db)
else:
    logger.warning("Unhandled Stripe event type: %s", event.type)
db.add(StripeEvent(id=event.id, event_type=event.type))
```

The `stripe_events` row insertion and 200 return stay exactly as-is.

**3. Topup endpoint (`backend/app/routes/billing.py`):**

Add a `POST /billing/topup` endpoint:

- Request body: `TopupRequest { amount_micros: int }` (Pydantic).
- Response: `TopupResponse { client_secret: str, amount_micros: int }`.
- Dep: `Depends(require_verified)` (import from `app.dependencies`).
- Rate limit: `@limiter.limit(settings.TOPUP_RATE_LIMIT)`.
- Validation:
  - If `amount_micros < BILLING_TOPUP_MIN_MICROS` → `validation_error("Amount below minimum $<X.XX>.")`.
  - If `amount_micros > BILLING_TOPUP_MAX_MICROS` → `validation_error("Amount above maximum $<X.XX>.")`.
- Lazy Customer creation: if `user.stripe_customer_id is None`, call `await create_customer(user, db)` and assign the returned id to `user.stripe_customer_id`.
- Idempotency key: `f"topup:{user.id}:{body.amount_micros}:{int(time.time() // 60)}"`.
- Stripe PaymentIntent creation:
  ```python
  kwargs = {
      "customer": user.stripe_customer_id,
      "amount": body.amount_micros // 10_000,  # cents
      "currency": settings.BILLING_CURRENCY,
      "metadata": {"user_id": str(user.id)},
  }
  if settings.STRIPE_TAX_ENABLED:
      kwargs["automatic_tax"] = {"enabled": True}
  intent = stripe.PaymentIntent.create(**kwargs, idempotency_key=idempotency_key)
  ```
- Return `TopupResponse(client_secret=intent.client_secret, amount_micros=body.amount_micros)`.

**4. Balance endpoint (`backend/app/routes/billing.py`):**

Add a `GET /billing/balance` endpoint:

- No body; no rate limit.
- Dep: `Depends(get_current_user)` (authed, not verified-gated).
- Response: `BalanceResponse { balance_micros: int, formatted: str }`.
- Implementation: `balance = await get_balance_micros(user.id, db); return BalanceResponse(balance_micros=balance, formatted=format_balance(balance))`.

**5. Config (`backend/app/config.py`):**

- Add `TOPUP_RATE_LIMIT: str = "10/minute"` alongside the existing rate-limit settings.

**6. `.env.example` — no changes.** Chassis defaults cover everything.

**7. Tests (`backend/tests/test_billing_topup.py` — new file):**

Covers topup + balance + webhook-handler dispatch. Unit-level; Stripe API calls mocked via `monkeypatch` on `stripe.PaymentIntent.create` and `stripe.Customer.create`.

- `test_topup_endpoint_requires_verified` — unverified user → 403.
- `test_topup_endpoint_requires_auth` — no token → 401.
- `test_topup_endpoint_creates_payment_intent_with_metadata` — verified user, valid amount → 200 with client_secret; verify `stripe.PaymentIntent.create` was called with `metadata={"user_id": "<id>"}`, `amount` in cents, `idempotency_key` matching the format, `currency="usd"`.
- `test_topup_endpoint_rejects_amount_below_min` — `amount_micros=100000` (= $0.10) → 422.
- `test_topup_endpoint_rejects_amount_above_max` — `amount_micros=600_000_000` (= $600) → 422.
- `test_topup_endpoint_lazy_creates_customer_if_missing` — user with `stripe_customer_id=None` → topup works; verify `stripe.Customer.create` was called; verify `user.stripe_customer_id` is populated after.
- `test_topup_endpoint_uses_existing_customer_id_if_present` — user with `stripe_customer_id="cus_existing"` → topup does NOT call `stripe.Customer.create`.
- `test_balance_endpoint_returns_zero_for_new_user` — authed new user → `{balance_micros: 0, formatted: "$0.00"}`.
- `test_balance_endpoint_returns_correct_format_for_whole_cents` — ledger grant of $1.23 → `formatted="$1.23"`.
- `test_balance_endpoint_returns_correct_format_for_sub_cent` — ledger grant of $0.0034 → `formatted="$0.0034"`.
- `test_balance_endpoint_sums_multiple_entries` — two grants + one debit → balance reflects sum.
- `test_balance_endpoint_requires_auth` — no token → 401.
- `test_handle_payment_intent_succeeded_grants_balance` — mock PI event with metadata.user_id + amount → handler grants correctly; ledger row inserted with reason `topup` and stripe_event_id.
- `test_handle_payment_intent_succeeded_skips_missing_metadata` — event without metadata.user_id → handler logs warning + returns; no ledger row.
- `test_handle_payment_intent_succeeded_skips_invalid_user_id` — metadata.user_id="abc" → handler logs warning + returns.
- `test_handle_payment_intent_succeeded_skips_zero_amount` — amount=0 → handler skips.
- `test_webhook_dispatches_to_registered_handler` — valid signed `payment_intent.succeeded` event → handler invoked; stripe_events row inserted.
- `test_webhook_duplicate_event_skips_handler_call` — same event.id posted twice → handler invoked exactly ONCE (not twice); stripe_events row unchanged.
- `test_webhook_unregistered_event_type_still_records` — `customer.updated` event (no handler yet) → logs warning, inserts stripe_events row, returns 200.

**8. Smoke script (`backend/scripts/smoke_billing.py` — new):**

Follow the existing smoke pattern (argparse with `--base-url`, httpx, sys.exit(1) on fail, print SMOKE OK on pass). Steps:
1. Register a fresh smoke user (`smoke+billing-<uuid>@carddroper.com`).
2. Authenticate via register response (Bearer).
3. `GET /billing/balance` with Authorization header.
4. Assert response: `balance_micros == 0`, `formatted == "$0.00"` (strict match).
5. Cleanup: `POST /auth/logout`.

Do NOT attempt topup in smoke (no card infrastructure in staging test path). Phase 1 local handles topup end-to-end with Stripe CLI.

**9. Backend API reference (`doc/reference/backend-api.md`):**

Expand the existing Billing section:
- `POST /billing/topup` — verified + rate-limited. Request body: `{amount_micros: int}`. Response: `{client_secret, amount_micros}`. Creates Stripe PaymentIntent with `metadata.user_id` + idempotency key. Lazy-creates Stripe Customer if absent.
- `GET /billing/balance` — authed. Response: `{balance_micros, formatted}`. Sums `balance_ledger`.
- Update `POST /billing/webhook` note: "Handlers registered via `EVENT_HANDLERS` dispatch registry; currently handles `payment_intent.succeeded`. Unregistered types logged and recorded; no error."

### Phase 0b — frontend (frontend-builder)

Read ticket §Design decisions #1, #3, #5, #6, #10, #11, #12 for the UI contracts. Follow them verbatim.

**Repository root:** /Users/johnxing/mini/postapp. Currently on `main`. Do NOT touch `backend/`.

**1. Dependencies (`frontend/package.json`):**

Add:
- `@stripe/stripe-js` — pin to a recent stable (check current stable at implementation time; ~5.x as of 2026-04).
- `@stripe/react-stripe-js` — matching major with stripe-js.

No other dependency changes.

**2. Environment wiring — three files:**

- `frontend/.env.example` — add `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=` after the existing `NEXT_PUBLIC_API_BASE_URL`.
- `docker-compose.yml` — under `services.frontend.build.args`, add `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY: ${STRIPE_PUBLISHABLE_KEY}` (reads from root `.env` or environment at build time).
- `cloudbuild.yaml` — in the frontend docker-build step's `args`, add `- --build-arg` and `- NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$_STRIPE_PUBLISHABLE_KEY` (substitution variable; user configures in Cloud Build trigger).

Root-level `.env` (dev) and Cloud Build substitution variable (staging) wiring is the user's operational task, not part of this dispatch — the frontend-builder just ensures the build-arg plumbing is in place.

**3. Stripe singleton (`frontend/lib/stripe.ts` — new):**

```ts
import { loadStripe, type Stripe } from "@stripe/stripe-js";

let stripePromise: Promise<Stripe | null> | null = null;

export function getStripe(): Promise<Stripe | null> {
  if (!stripePromise) {
    const key = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;
    if (!key) {
      throw new Error(
        "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY is not set. " +
        "Billing is enabled but the frontend can't initialize Stripe Elements.",
      );
    }
    stripePromise = loadStripe(key);
  }
  return stripePromise;
}
```

**4. Balance hook (`frontend/hooks/useBalance.ts` — new):**

```ts
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface BalanceResponse {
  balance_micros: number;
  formatted: string;
}

export const BALANCE_QUERY_KEY = ["billing", "balance"] as const;

export function useBalance(): UseQueryResult<BalanceResponse> {
  return useQuery<BalanceResponse>({
    queryKey: BALANCE_QUERY_KEY,
    queryFn: () => api.get<BalanceResponse>("/billing/balance"),
    staleTime: 30_000,
    retry: false,
  });
}
```

**5. Balance display (`frontend/components/billing/BalanceDisplay.tsx` — new):**

Minimal component — renders the `formatted` string with optional wrapper styling. Accepts a `className` prop for projects to override visual placement. Shows a skeleton/dash when `isLoading`.

```tsx
"use client";

import { useBalance } from "@/hooks/useBalance";

export function BalanceDisplay({ className }: { className?: string }) {
  const { data, isLoading, isError } = useBalance();
  if (isLoading) return <span className={className}>—</span>;
  if (isError || !data) return <span className={className}>—</span>;
  return <span className={className}>{data.formatted}</span>;
}
```

**6. TopupForm (`frontend/components/billing/TopupForm.tsx` — new, client component):**

See Design decisions #3, #5, #6 for the full behavior. Key shape:

- Props: `{ presetAmounts?: number[]; minAmountMicros?: number; maxAmountMicros?: number; onSuccess?: () => void; }`. Defaults: `[500_000, 2_000_000, 5_000_000]`, `500_000`, `500_000_000`, `undefined`.
- State: `selectedAmount: number | null`, `customAmount: string`, `clientSecret: string | null`, `error: string | null`, `status: "idle" | "fetching_secret" | "ready" | "submitting" | "confirmed_polling" | "success" | "processing"`.
- Two-phase flow:
  - **Phase A (select amount)**: render preset buttons + custom amount input + "Continue" button. On Continue:
    - Validate amount in range (show inline error if not).
    - Status → `"fetching_secret"`.
    - `const resp = await api.post<{client_secret: string; amount_micros: number}>("/billing/topup", { amount_micros: finalAmount });`
    - On success: set `clientSecret`; status → `"ready"`.
    - On `ApiError`: map to user message. 403 → "Please verify your email first." 422 → show the backend message. 429 → "Too many requests, please wait a moment." Other → generic.
  - **Phase B (enter card + confirm)**: render `<Elements stripe={getStripe()} options={{clientSecret, appearance: {theme: "stripe"}}}><InnerForm ... /></Elements>`. Inner form uses `useStripe` + `useElements` + `<PaymentElement />`. On submit:
    - Status → `"submitting"`.
    - `const result = await stripe.confirmPayment({elements, redirect: "if_required"});`
    - On `result.error`:
      - `type === "card_error"` → show `result.error.message` (Stripe's user-facing message).
      - Other types → generic "Something went wrong, please try again."
      - Status → `"ready"` (lets user retry without re-entering card).
    - On success (no error):
      - Status → `"confirmed_polling"`.
      - Capture `priorBalance = queryClient.getQueryData(BALANCE_QUERY_KEY)?.balance_micros ?? 0`.
      - Invalidate `BALANCE_QUERY_KEY`.
      - Poll: loop up to 10 iterations, each iteration: `await new Promise(r => setTimeout(r, 1000));` + refetch `/billing/balance` via `queryClient.refetchQueries(BALANCE_QUERY_KEY)` + check `(newBalance - priorBalance) >= amount_micros`.
      - If match: status → `"success"`; call `onSuccess?.()`.
      - If timeout without match: status → `"processing"` (webhook may still be en route).
- Render based on status:
  - `"idle"` / `"fetching_secret"` / `"ready"`: the respective UI phase, plus any error banner.
  - `"submitting"`: the card form + disabled Pay button with spinner.
  - `"confirmed_polling"`: "Processing your payment…" spinner.
  - `"success"`: "Balance updated ✓" with new balance visible (since we invalidated, useBalance elsewhere also refreshes).
  - `"processing"`: "Payment received. Your balance will update shortly." (no error; just slower-than-expected webhook).

- Input validation: custom-amount input is numeric only, disallows negatives, accepts up to 2 decimal places (convert to micros via `Math.round(parseFloat(value) * 1_000_000)`). Shows inline error on blur if out of range.

- All preset buttons show `$X` format (divide by `1_000_000` then format). Active (selected) preset shows highlighted visual state.

**7. Billing page (`frontend/app/(app)/billing/page.tsx` — new):**

Client component. Hosts the topup flow + shows current balance prominently.

```tsx
"use client";

import { BalanceDisplay } from "@/components/billing/BalanceDisplay";
import { TopupForm } from "@/components/billing/TopupForm";

export default function BillingPage() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-2">Billing</h1>
      <p className="text-sm text-gray-600 mb-6">
        Current balance: <BalanceDisplay className="font-medium text-gray-900" />
      </p>
      <div className="border-t border-gray-200 pt-6">
        <h2 className="text-lg font-semibold mb-4">Add funds</h2>
        <TopupForm />
      </div>
    </div>
  );
}
```

No project-specific copy; generic chassis language.

**8. Add Billing link to ProfileMenu (`frontend/components/app-shell/ProfileMenu.tsx`):**

Between the existing Settings section label and the divider-before-Logout, insert:

```tsx
<Link
  href="/app/billing"
  role="menuitem"
  onClick={() => setOpen(false)}
  className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
>
  Billing
</Link>
```

Don't forget `import Link from "next/link"` at the top. The `onClick={() => setOpen(false)}` closes the menu after navigation (common Stripe/Linear pattern — don't leave stale menu open).

**9. Tests — none automated** (no frontend test harness yet). Gates:

- `npm run lint` clean.
- `npx tsc --noEmit` clean.
- `npm run build` succeeds.

Manual browser verification is Phase 1 (user-run).

### Phase 1 — user local test

**Base (required before merge):**

1. Ensure `BILLING_ENABLED=true` + Stripe test-mode keys in `backend/.env` (reuse setup from 0021 Phase 1 stretch).
2. Root `.env` (dev) needs `STRIPE_PUBLISHABLE_KEY=pk_test_...` for docker-compose to pass it as the frontend build arg.
3. `stripe listen --project-name carddroper --forward-to localhost:8000/billing/webhook` in a separate terminal (from 0021 setup).
4. `docker-compose up -d --build` (both containers).
5. Log in → click profile avatar → see `Billing` link under Settings → click → land on `/app/billing`.
6. See "Current balance: $0.00" (or whatever the fresh user has from signup/verify bonus).
7. Click preset `$20` button. Click "Continue." Card form appears.
8. Use test card `4242 4242 4242 4242`, any future expiry (e.g. `12/34`), any 3-digit CVC, any ZIP. Click "Pay."
9. "Processing your payment…" spinner appears briefly. Then "Balance updated ✓" with new balance $20.00 (or prior + $20).
10. Stripe CLI terminal shows the `payment_intent.succeeded` webhook landing with 200.
11. Check DB: `docker-compose exec db psql -U carddroper -d carddroper -c "SELECT reason, amount_micros / 1000000.0 as dollars FROM balance_ledger WHERE user_id = <your_id> ORDER BY created_at DESC LIMIT 5;"` — confirm `topup` row with `20.000000`.

**Error handling test:**

12. Repeat with card `4000 0000 0000 0002` (generic decline). Click Pay → see "Your card was declined." inline error. Form stays mounted. Re-enter a valid test card → succeeds.

**Range validation test:**

13. Enter custom amount $0.25 → inline error "Below minimum $0.50."
14. Enter custom amount $600 → inline error "Above maximum $500.00."

**Free-form test:**

15. Enter custom amount $7.50 → Continue → card form → Pay → balance becomes (prior + $7.50). DB row shows `amount_micros=7500000`.

**Idempotency sanity (optional):**

16. Force a double-click rapidly on "Continue" (click + click within 100ms). Only ONE PaymentIntent should be created in Stripe Dashboard under test mode. If two appear with different IDs, we have a double-click problem (the button should be disabled during the POST).

**Regression checks:**

17. `/app` still renders correctly; profile popover still works; Logout still works; 0016.2 redirect + 0016.5 LoadingScreen still fire.
18. Marketing `/` untouched.
19. Responsive drawer behavior from 0022 still works on narrow (the Billing link inside ProfileMenu navigates + closes drawer).

### Phase 2 — user staging

1. Set the Cloud Build substitution variable `_STRIPE_PUBLISHABLE_KEY=pk_test_...` in the Cloud Build trigger config (user task in GCP Console).
2. Optionally set staging `BILLING_ENABLED=true` + `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` in the Cloud Run env (Secret Manager) — ONLY if you want billing live on staging. Otherwise leave `BILLING_ENABLED=false` and this ticket's staging check is limited to smoke + visual.
3. Push to `main`. Cloud Build redeploys both services.
4. Smoke battery:
   ```bash
   cd backend
   .venv/bin/python scripts/smoke_healthz.py
   .venv/bin/python scripts/smoke_auth.py --expected-cookie-domain .staging.carddroper.com
   .venv/bin/python scripts/smoke_cors.py
   .venv/bin/python scripts/smoke_verify_email.py
   .venv/bin/python scripts/smoke_billing.py
   ```
   All five should pass.
5. If `BILLING_ENABLED=true` on staging: visit `https://staging.carddroper.com/app/billing`, log in, walk through a topup with a Stripe test card. Verify balance updates.
6. If `BILLING_ENABLED=false` on staging: `/app/billing` is still reachable via the link (frontend doesn't gate), but topup will 404 (router not mounted). This is an acceptable state while live billing isn't wanted on staging — we'll validate end-to-end in prod once ready.

## Verification

**Automated (backend-builder Phase 0a report):**

- `.venv/bin/pytest` green — all new tests in `test_billing_topup.py` pass; existing suites unchanged.
- `ruff check` + `ruff format --check` clean.
- Paste final definitions of:
  - `EVENT_HANDLERS` registry + `register()` decorator
  - `handle_payment_intent_succeeded`
  - `POST /billing/topup` handler
  - `GET /billing/balance` handler
  - Updated webhook dispatch block
- Confirm `smoke_billing.py` matches the existing smoke pattern + exits 0 against local dev.
- `pytest` / `ruff check` / `ruff format` summary lines.

**Automated (frontend-builder Phase 0b report):**

- `npm run lint` / `npx tsc --noEmit` / `npm run build` clean.
- Paste final:
  - `lib/stripe.ts`
  - `hooks/useBalance.ts`
  - `components/billing/BalanceDisplay.tsx`
  - `components/billing/TopupForm.tsx` (full — this is the complex one)
  - `app/(app)/billing/page.tsx`
  - The single-line diff in `ProfileMenu.tsx` for the Billing link insertion.
- Confirm `package.json` diff (just the two Stripe deps added).
- Confirm env wiring: `.env.example`, `docker-compose.yml`, `cloudbuild.yaml` all updated consistently.
- Confirm untouched: `(app)/layout.tsx` from 0022, `(marketing)/layout.tsx`, `(auth)/layout.tsx`, `LogoutButton`, `AppSidebar`, `context/auth.tsx`, `lib/api.ts`.

**Functional (user, Phase 1):**

- Steps 1-19 pass locally.

**Staging (user, Phase 2):**

- Smoke battery green (5 scripts including new `smoke_billing.py`).
- Optional: live test mode topup on staging if `BILLING_ENABLED=true` there.

## Chassis implications

0023 completes the PAYG half of the billing chassis. After this ticket:

- Any project adopting this chassis gets a complete topup UI by setting `BILLING_ENABLED=true` + Stripe publishable key + using `<TopupForm />` in their own page (or rendering `/app/billing` as-is).
- Handler registry pattern scales for 0024 (subscription events) + 0025 (refund events) — each new handler is an import + decorator.
- `BalanceDisplay` + `useBalance` available for any project UI that wants to show balance.
- Lazy Customer creation pattern means `BILLING_ENABLED` can be flipped on at any time — existing users get Customers created the moment they touch a billing endpoint. No migration / backfill script needed.

No new chassis-contract.md entries — the existing `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` invariants from 0021 cover the new surface.

## Report

Backend-builder (Phase 0a):

- Files modified + one-line what-changed each.
- Pasted artifacts per §Verification.
- pytest + ruff summary lines.
- Any deviation from the brief, with reasoning.

Frontend-builder (Phase 0b):

- Files modified + one-line what-changed each.
- Pasted artifacts per §Verification.
- lint / tsc / build summary lines.
- Any deviation.

Orchestrator (on close):

- User Phase 1 base + error-handling + range + free-form + regression outcomes.
- User Phase 2 staging outcome (smoke battery; live-test-mode topup if applicable).

## Resolution

*(filled in by orchestrator after user confirms Phase 1 + Phase 2 pass)*
