---
id: 0025
title: customer portal redirect — POST /billing/portal-session + Manage subscription button on /app/subscribe
status: open
priority: medium (chassis completion; final ticket of the 0024.x billing chassis arc; provides recovery UX for past_due subscriptions and self-service cancellation per payments.md §UX split)
found_by: chassis arc completion 2026-04-29 — payments.md §UX split designates Stripe Customer Portal as the canonical surface for: payment method updates, subscription cancellation, invoice history, billing detail updates. payments.md line 420 already specifies the endpoint shape POST /billing/portal-session. 0024.x verified the chassis correctly handles past_due entry (0024.15) and recovery via invoice.paid post-PM-update (0024.14). 0025 ships the redirect endpoint and minimum frontend hookup so users can actually reach Portal — completing the recovery loop.
---

## Audit (per `doc/operations/audit-template.md`)

### 1. User-visible action

User clicks "Manage subscription" button on `/app/subscribe` (visible when their subscription row exists in any state). Frontend POSTs to `/billing/portal-session` with `return_url=window.location.href`. Backend creates a one-shot Stripe Portal session URL; frontend redirects user to Stripe-hosted Portal at `billing.stripe.com/...`. User performs actions (update PM, cancel subscription, view invoices). User clicks "Back to {App}" → Stripe redirects to `return_url`. Chassis observes the side effects (PM updates, cancel flag, etc.) via existing webhook handlers — no new chassis subscription required.

### 2. Full request flow trace

```
1. User clicks "Manage subscription" on /app/subscribe
2. Frontend: POST /billing/portal-session
              { return_url: "https://staging.carddroper.com/app/subscribe" }
3. Backend (require_billing_user):
   a. Verify auth
   b. If user.stripe_customer_id is None: lazy-create via billing.create_customer
      (matches setup-intent + subscribe endpoint patterns)
   c. Validate return_url starts with settings.FRONTEND_BASE_URL → 422 if not
      (security: prevent open-redirect attack)
   d. stripe.billing_portal.Session.create(
          customer=user.stripe_customer_id,
          return_url=return_url,
          configuration=settings.BILLING_PORTAL_CONFIGURATION_ID or None,
      )
   e. Return { url: session.url }
4. Frontend: window.location.href = response.url
5. User on billing.stripe.com/... — performs actions
6. User clicks "Back to {App}" → Stripe redirects to return_url
7. (Async) Stripe fires webhooks for any state changes the user made:
   - PM update: customer.updated (chassis does NOT subscribe — see Q3.5)
   - PM updates also schedule dunning retry; (eventually) invoice.paid →
     chassis handle_invoice_paid posts subscription_reset, transitions to active
   - Cancel-at-period-end: customer.subscription.updated with
     cancel_at_period_end=true → chassis handle_subscription_updated syncs flag
   - Cancel-immediately: customer.subscription.deleted → chassis marks canceled
8. Chassis state catches up to Stripe via existing webhook handlers
   (no new handlers in this ticket)
```

### 3. Endpoint properties

| Step | Endpoint | Idempotency | DB writes | Stripe state |
|---|---|---|---|---|
| 2 | POST /billing/portal-session | NONE (per-request pattern; Portal Sessions are one-shot URLs) | lazy-creates `user.stripe_customer_id` if absent | Creates ephemeral Portal Session |
| 7 | (async webhooks — existing handlers) | per `stripe_events.id` UNIQUE (per 0023.2) | existing chassis behavior | varies per event |

### 3.5. Cross-writer assumption check

This ticket adds a new endpoint that lazy-creates `user.stripe_customer_id`. Three existing writers do the same pattern (`POST /billing/setup-intent`, `POST /billing/subscribe`, `POST /auth/register` when BILLING_ENABLED=true). All four call the same `billing.create_customer` primitive. **No writer's assumption is invalidated** — they're identical paths sharing one primitive.

The new endpoint does NOT subscribe to any new Stripe events. Portal-driven actions trigger events the chassis already handles:
- `customer.subscription.updated` (cancel-at-period-end flip) — handled per 0024.5/7 Path B
- `customer.subscription.deleted` (cancel-immediate) — handled
- `invoice.paid` (post-PM-update dunning retry) — handled per 0024.11/12

`customer.updated` and `payment_method.*` events are NOT subscribed — chassis behavior is correct without them (only the OUTCOME — failed-invoice-now-paid — matters, which `invoice.paid` already covers).

### 4. Consumability check

- **Portal Session**: ephemeral one-shot URL (Stripe expires it shortly after creation; not a persistent resource). Not "consumable" in the chassis idempotency-policy sense. Per-request pattern (no `idempotency_key=`) is correct.
- **No other resources** created or modified by this endpoint.

### 5. Adversarial scenario

Most likely failure modes:

1. **Portal not configured in Stripe Dashboard** → `Session.create` raises `stripe.error.InvalidRequestError("No portal configuration created")` → endpoint should surface as 503 with a clear message (chassis can't fix; adopter must configure in Dashboard)

2. **Open-redirect attack** — malicious `return_url=https://phishing.example.com` → backend prefix-validation against `FRONTEND_BASE_URL` rejects with 422

3. **User has no `stripe_customer_id` (registered before billing was enabled)** → lazy-create handles this (matches existing chassis pattern)

4. **`return_url` not supplied by frontend** → falls back to `f"{FRONTEND_BASE_URL}/app/subscribe"` default

5. **`BILLING_PORTAL_CONFIGURATION_ID` env var set but invalid** → `Session.create` raises specific error → 500 with clear message

6. **Stripe rate limit on Session creation** → frontend shows "try again" message (handled via standard error path)

7. **User updates PM via Portal but the new PM ALSO fails** → past_due continues; eventually canceled. Chassis handles via existing handlers (0024.15 verified). Out of scope here — recovery is async.

### 6. Test coverage

Required tests (`backend/tests/test_billing.py` or similar):

- `test_portal_session_requires_auth` — 401 when not logged in
- `test_portal_session_creates_session_with_default_return_url` — no `return_url` in body → uses `f"{FRONTEND_BASE_URL}/app/subscribe"`
- `test_portal_session_validates_return_url_against_frontend_base_url` — `return_url=https://evil.example.com` → 422
- `test_portal_session_lazy_creates_customer` — user with no `stripe_customer_id` → customer is created and persisted
- `test_portal_session_uses_existing_customer` — user with `stripe_customer_id` → reuses it
- `test_portal_session_passes_configuration_id_when_set` — env var passed through to Stripe
- `test_portal_session_omits_configuration_id_when_empty` — no `configuration=` kwarg passed (uses Stripe account default)
- `test_portal_session_returns_url_from_stripe` — response body contains `{ url: "<stripe-portal-url>" }`

Mocks `stripe.billing_portal.Session.create`. Use `spec=`-restricted MagicMock per 0024.12 discipline.

## Context

`payments.md` already designates Customer Portal as the canonical surface for PM update + cancel + invoice history (line 73-87 §UX split). Endpoint name `POST /billing/portal-session` is already specified at line 420. 0025 implements the chassis spec; no design decisions to make beyond what the spec already settles.

The chassis renewal flow has been empirically validated end-to-end:
- 0024.14: success path (Stripe test clocks → real `invoice.paid` → chassis advances period + posts subscription_reset)
- 0024.15: failure path (real `invoice.payment_failed` → chassis transitions past_due) + recovery (real `invoice.pay()` after PM restore → chassis returns to active)

0025 connects the user UX to that verified backend: when sub is past_due, user clicks "Manage subscription" → Portal → updates PM → Stripe's dunning retry uses new PM → chassis returns user to active.

## Design decisions (pre-committed per user discussion)

### Endpoint shape

```python
@router.post("/portal-session", response_model=PortalSessionResponse)
async def portal_session(
    request: Request,
    body: PortalSessionRequest,
    user=Depends(require_billing_user),
    db: AsyncSession = Depends(get_db),
) -> PortalSessionResponse:
    init_stripe()

    # Lazy-create customer (matches setup-intent + subscribe pattern)
    if user.stripe_customer_id is None:
        customer_id = await billing.create_customer(user, db)
        user.stripe_customer_id = customer_id
        await db.commit()

    # Resolve return URL with prefix validation
    return_url = body.return_url or f"{settings.FRONTEND_BASE_URL}/app/subscribe"
    if not return_url.startswith(settings.FRONTEND_BASE_URL):
        raise validation_error(
            f"return_url must be on our domain ({settings.FRONTEND_BASE_URL})"
        )

    # Build kwargs; pass configuration only if env var is set
    kwargs = {
        "customer": user.stripe_customer_id,
        "return_url": return_url,
    }
    if settings.BILLING_PORTAL_CONFIGURATION_ID:
        kwargs["configuration"] = settings.BILLING_PORTAL_CONFIGURATION_ID

    try:
        session = stripe.billing_portal.Session.create(**kwargs)
    except stripe.error.InvalidRequestError as exc:
        if "portal configuration" in str(exc).lower():
            raise validation_error(
                "Stripe Customer Portal is not configured. "
                "Configure at Stripe Dashboard → Settings → Billing → Customer portal."
            )
        raise

    return PortalSessionResponse(url=session.url)
```

### New env var

`BILLING_PORTAL_CONFIGURATION_ID: str = ""` in `app/config.py`.

- Default empty → Stripe uses the account's default Portal configuration
- Set to a specific configuration ID (e.g., `bpc_1ABC...`) for project-layer-specific Portal customization
- Adopters: configure once in Stripe Dashboard, paste the ID into staging/production env vars
- Not a chassis-contract entry (not a startup-time invariant)

### Pydantic models

```python
class PortalSessionRequest(BaseModel):
    return_url: Optional[str] = None

class PortalSessionResponse(BaseModel):
    url: str
```

### Frontend scope (minimal)

ONE button on `/app/subscribe`:
- Visible when user has a subscription (per `GET /billing/subscription` returning a row)
- Hidden during `incomplete` state (recovery in progress; Portal won't help)
- Click → POST `/billing/portal-session` with `return_url=window.location.href` → redirect to response.url
- Loading state during the API call

Implementation: new component `frontend/components/billing/ManageSubscriptionButton.tsx`, rendered conditionally in `frontend/app/(app)/app/subscribe/page.tsx` (or composed into `SubscriptionDisplay.tsx` — agent's call based on existing structure).

API client function: `frontend/lib/api/billing.ts` (or wherever billing API lives) — `createPortalSession(returnUrl?: string)`.

### Out of scope

- **Past-due banner** — defer to 0025.1 if Carddroper needs it for production users
- **ProfileMenu "Manage subscription" item** — project-layer concern
- **Settings/billing page as separate route** — project-layer
- **Custom Portal branding** — Stripe Dashboard task, not chassis
- **Production-mode Portal Dashboard config** — separate deployment task; verified during 0025 Phase 1 in test mode only
- **Webhook subscription for `customer.updated` / `payment_method.*`** — chassis behavior is correct without; existing handlers cover the OUTCOME path (`invoice.paid` for recovery, `customer.subscription.updated` for cancel)
- **`BILLING_PORTAL_CONFIGURATION_ID` validator at startup** — value is optional; chassis boots fine without
- **Async-recovery messaging** ("we'll retry your payment soon") — project-layer UX

## Acceptance

### Phase 0a — backend (backend-builder)

**Repository root:** /Users/johnxing/mini/postapp. On `main`. Backend-only — do NOT touch `frontend/`.

**1. Add `BILLING_PORTAL_CONFIGURATION_ID` to `app/config.py`** — default `""`. Add a one-line comment explaining its purpose (chassis-tunable; default empty = use Stripe account default).

**2. Implement `POST /billing/portal-session`** in `app/routes/billing.py` per Design decisions §Endpoint shape. Use `validation_error` from `app/errors.py` for 422s. Pass `init_stripe()` first as other endpoints do.

**3. Add `PortalSessionRequest` / `PortalSessionResponse` Pydantic models** alongside existing models in `app/routes/billing.py` (the file's convention).

**4. Add tests** per Audit §6 (8 tests). Use `MagicMock(spec=...)` for Stripe response mocking per 0024.12 discipline.

**5. Update `doc/systems/payments.md`** §Chassis-exposed HTTP endpoints — expand the existing `POST /billing/portal-session` line (around line 420) with the contract: request body `{return_url?: string}`, response `{url: string}`, validation rules, and lazy-customer-create behavior. Add §8 Customer Portal flow at the end of §Flows section if natural; otherwise the endpoint summary is enough.

**6. Update `cloudbuild.yaml`** — add `BILLING_PORTAL_CONFIGURATION_ID=` (empty default) to the `--set-env-vars` line so staging Cloud Run has the var declared. Empty string means "use Stripe account default", which is what the user has configured in test mode.

**PAUSE-and-report (Phase 0a):**

- The chassis's existing test file structure for billing routes makes test placement non-obvious (e.g., test_billing.py vs a new test_billing_portal.py). PAUSE — confirm or use existing convention.
- `billing.create_customer` signature differs from what the spec assumes. PAUSE.
- `validation_error` doesn't accept the message format the design uses. PAUSE.

### Phase 0b — frontend (frontend-builder, dispatched in parallel with 0a)

**Repository root:** /Users/johnxing/mini/postapp. On `main`. Frontend-only — do NOT touch `backend/`.

**1. Add API client function** in `frontend/lib/api/billing.ts` (or the project's billing API conventions location):

```typescript
export async function createPortalSession(returnUrl?: string): Promise<{ url: string }> {
  const body = returnUrl ? { return_url: returnUrl } : {};
  const response = await api.post<{ url: string }>("/billing/portal-session", body);
  return response;
}
```

**2. Add `ManageSubscriptionButton` component** in `frontend/components/billing/ManageSubscriptionButton.tsx`:

- Uses `useSubscription` hook (or whatever hook exists for subscription state)
- Renders nothing if no subscription
- Renders nothing if subscription is `incomplete` (recovery in progress)
- Otherwise renders a button: "Manage subscription"
- onClick: call `createPortalSession(window.location.href)`, set loading state, on success `window.location.href = response.url`
- Error path: surface a generic "Something went wrong" via existing error UX

**3. Render the button on `/app/subscribe`** — `frontend/app/(app)/app/subscribe/page.tsx`. Place below `SubscriptionDisplay` (or wherever fits the existing layout best).

**4. Tests** — same posture as 0024.9/0024.15: no frontend test runner is installed; manual smoke is the coverage. Run `npm run lint && npm run typecheck && npm run build` and confirm clean.

**PAUSE-and-report (Phase 0b):**

- The chassis's `useSubscription` hook (or equivalent) doesn't exist or has a different shape. PAUSE — surface the actual hook location.
- The API client wrapper expects a different request shape. PAUSE.
- The `SubscriptionDisplay` component's existing structure makes button placement awkward. PAUSE.

### Phase 0c — orchestrator post-dispatch audit

After both agents report done, orchestrator runs:

1. `grep -n "portal-session\|portal_session" backend/app/routes/billing.py` — endpoint registered
2. `grep -n "FRONTEND_BASE_URL" backend/app/routes/billing.py | grep -i portal` — return_url validation present
3. `grep -n "BILLING_PORTAL_CONFIGURATION_ID" backend/app/config.py` — env var declared
4. `grep -n "BILLING_PORTAL_CONFIGURATION_ID" cloudbuild.yaml` — staging deploy includes the var
5. `grep -n "test_portal_session" backend/tests/` — at least 5 tests present
6. `grep -rn "createPortalSession\|portal-session" frontend/` — frontend wiring present
7. `grep -rn "ManageSubscription\|Manage subscription" frontend/components/billing/ frontend/app/` — button component exists
8. `git diff origin/main..HEAD --name-only -- backend/app/billing/handlers/` — must be empty (no new webhook handlers)
9. `cd backend && BILLING_ENABLED=true .venv/bin/pytest tests/ -q` — zero failures (regression check)
10. `cd backend && BILLING_ENABLED=false .venv/bin/pytest tests/ -q` — zero failures
11. `cd frontend && npm run lint && npm run typecheck && npm run build` — clean

If any check fails, the ticket is NOT done — re-dispatch with the specific gap.

### Phase 1 — user manual smoke (Tier C — Stripe Dashboard manipulation)

After all phases green:

1. `docker-compose up -d --force-recreate --build backend frontend`
2. **Confirm Portal is enabled in Stripe test mode** (you've already verified)
3. Log in as a user with an active subscription (the test-clock fixture user 64 works)
4. On `/app/subscribe`, verify "Manage subscription" button is visible
5. Click button → page redirects to `billing.stripe.com/...`
6. Verify Portal page shows: subscription details, invoice history, "Update payment method" + "Cancel subscription" controls
7. Click "Back to {App}" or "Return to ..." link → lands on `/app/subscribe`
8. Test cancel-at-period-end:
   - Click "Manage" → Portal → "Cancel subscription" → confirm
   - Returns to `/app/subscribe`
   - Wait ~5s for `customer.subscription.updated` webhook
   - Verify in psql: `cancel_at_period_end=true`, status still `active` (cancel takes effect at period_end)
9. Test PM update:
   - Click "Manage" → Portal → "Update payment method" → use Stripe test card `4242 4242 4242 4242`
   - Returns to `/app/subscribe`
   - Verify in Stripe Dashboard: customer's default PM is now the new card
   - Note: chassis status unchanged (PM update alone doesn't fire chassis-handled events)
10. Test invoice view:
    - Click "Manage" → Portal → "Invoice history" → click on a paid invoice → download PDF
    - Verify PDF includes the subscription line item
11. **Edge case** — open-redirect attempt (curl):
    ```bash
    curl -X POST https://api.staging.carddroper.com/billing/portal-session \
      -H "Authorization: Bearer <token>" \
      -H "Content-Type: application/json" \
      -d '{"return_url": "https://phishing.example.com"}'
    ```
    Expect: 422 with validation error.

### Phase 2 — staging

1. Push to main → Cloud Build redeploys
2. Confirm `BILLING_PORTAL_CONFIGURATION_ID` env var on Cloud Run (empty is OK)
3. Run smoke battery (existing `backend/scripts/smoke_*.py`)
4. **Configure Portal in production-mode Stripe Dashboard separately** when actually launching production (NOT this ticket's scope)

## Verification

**Automated (Phase 0a + 0b + 0c):**

- Two-state pytest summaries (both green)
- Ruff clean
- Frontend lint + typecheck + build clean
- All 11 orchestrator grep checks pass

**Functional (Phase 1):**

- Manage button visible when sub exists ✓
- Click redirects to Portal ✓
- Return URL works ✓
- Cancel-at-period-end syncs to chassis ✓
- PM update reflected in Stripe Dashboard ✓
- Open-redirect attack rejected ✓

## Chassis implications

After 0025:

- The chassis is **complete** for the 0024.x billing arc. All code paths in payments.md §UX split are implemented.
- Recovery loop is closed: past_due → user clicks Manage → Portal → updates PM → (Stripe dunning retry) → invoice.paid → chassis returns to active.
- Self-service cancellation is delegated to Stripe-hosted Portal per chassis spec.
- Customer Portal becomes the chassis's "everything else" surface for billing actions; chassis exposes only subscribe / topup / portal-session as billing endpoints.

No new chassis-contract entries (no new startup invariants).

## Report

Backend-builder (Phase 0a):

- Files modified + one-line purpose each.
- Diff of `app/routes/billing.py` (new endpoint + Pydantic models).
- Diff of `app/config.py` (env var).
- Diff of `cloudbuild.yaml` (staging env var).
- Diff of new tests.
- Diff of `payments.md`.
- Two-state pytest + ruff summary lines.
- Confirmation: existing endpoints (subscribe / setup-intent / topup / etc.) UNCHANGED.
- Confirmation: no new webhook handlers added.
- Anything PAUSED.

Frontend-builder (Phase 0b):

- File listing + one-line purpose each.
- Diff of API client addition.
- Diff of new ManageSubscriptionButton component.
- Diff of `/app/subscribe/page.tsx` (button placement).
- lint + typecheck + build summary lines.
- Confirmation: SubscribeForm flow UNCHANGED.
- Confirmation: SubscriptionDisplay component unchanged (or note any change explicitly).
- Anything PAUSED.

Orchestrator (Phase 0c):
- All 11 grep / pytest / build audit checks: PASS.

User (Phase 1):
- Manage button visible ✓
- Portal redirect works ✓
- Cancel-at-period-end syncs ✓
- PM update reflected ✓
- Invoice download works ✓
- Open-redirect attack rejected ✓

## Resolution

*(filled in by orchestrator after Phase 0a + 0b + 0c + Phase 1)*
