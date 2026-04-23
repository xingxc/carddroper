---
id: 0021
title: Stripe foundation (chassis) — balance ledger, billing primitives, webhook skeleton
status: open
priority: medium (unblocks 0022 topup + 0023 subscribe; zero user-facing behavior change on its own)
found_by: PLAN.md §10.6 Stripe layer; scoped per 2026-04-23 chassis reframe in `doc/systems/payments.md`
---

## Context

First ticket of the billing chassis (payments = third chassis subsystem after auth and email). Foundation-only: data model, primitives, webhook skeleton, auth-layer hooks. Zero user-facing surfaces beyond the webhook receiver. All project-specific decisions (tier prices, preset topup amounts, per-action debit cost, bonus amounts) are deferred until the chassis is complete — for 0021, every default is chassis-generic.

Everything user-facing (topup endpoint + Elements component, subscribe endpoint, balance endpoint, Customer Portal session, pricing page) lands in subsequent tickets (0022+). This ticket's deliverable is: the chassis is ready to have those surfaces bolted on without any schema migration or primitive refactor.

The full chassis design is in `doc/systems/payments.md` (chassis-only as of commit `eed4bf3`). Read that first — this ticket implements its contracts.

## Design decisions (pre-committed)

All major shape decisions are already in `payments.md`. What's pre-committed for the ticket itself:

- **Single-ticket scope.** Data model + primitives + webhook skeleton + auth integration + tests in one atomic deliverable. The primitives need the data model; the auth integration needs the primitives. Splitting creates a cascading dependency with no independent-ship value.
- **`BILLING_ENABLED=false` is the default.** Merging this ticket to main is a **zero-behavior-change** operation for any running deployment. Register still works exactly as it does today; no Stripe calls happen; no webhook endpoint is mounted. Billing is an opt-in at deploy time via env var.
- **Chassis-generic defaults only.** No carddroper-specific pricing, no opinionated bonus amounts, no preset topup values. Project layers override defaults at deploy time.
- **Unit-level tests with Stripe calls monkey-patched.** Real Stripe test-mode integration is optional manual verification in Phase 1, not blocking for merge. Chassis primitives and webhook signature path are fully unit-testable.
- **Webhook handlers are stubs.** The signature-verification + idempotency skeleton lands in 0021; specific event handlers (`payment_intent.succeeded`, `customer.subscription.*`, `invoice.paid`, `invoice.payment_failed`) land in the tickets that need them (0022 topup, 0023 subscribe). Unrecognized event types log a warning and return 200.
- **Non-breaking migration.** The `users.stripe_customer_id` column is nullable. Existing users have `NULL` until billing is enabled and they register (or a later backfill runs). No data backfill in this ticket.
- **Dual Customer creation strategy for future tickets.** Register creates the Customer eagerly when `BILLING_ENABLED=true`. Topup/subscribe (in later tickets) will lazily create if still `NULL` — handles the pre-billing-enabled users. This ticket implements the eager path; the lazy path lands with the first endpoint that needs a Customer.
- **Two new chassis-contract invariants.** When `BILLING_ENABLED=true`, both `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` must be non-empty. pydantic model_validators enforce; chassis-contract.md documents. Same pattern as the CORS (0015.5) and COOKIE_DOMAIN (0015.6) validators.

## Out of scope (explicit — to keep the chassis foundation atomic)

- `POST /billing/topup` — next ticket (0022).
- `POST /billing/subscribe` — ticket 0023.
- `GET /billing/balance` — rolled into whichever ticket needs it first (likely 0022).
- `POST /billing/portal-session` — separate ticket when a frontend UI consumes it.
- `billing.refund()` primitive — admin tooling, out of v1 chassis.
- Specific webhook event handlers (`payment_intent.succeeded`, subscription events, `invoice.*`, `charge.refunded`) — each lands with its feature ticket.
- Frontend changes — no UI in 0021.
- Any pricing values (subscription tier prices, preset topup buttons, per-action debit costs, signup/verify bonus amounts). Deferred per user directive on 2026-04-23.
- Backfill script for pre-existing users lacking `stripe_customer_id`. Separate operational deliverable when billing is flipped on in prod.
- Annual billing, multi-currency, Stripe Tax wiring (chassis supports tax via a single flag; actual enablement is deferred).
- `STARTER.md` chassis export. Deferred per `site-model.md` constraint 7.

## Acceptance

### Phase 0a — backend (backend-builder)

Before starting, read `doc/systems/payments.md` end-to-end. Every chassis contract (data model, reason vocabulary, primitive signatures, config knobs, webhook idempotency rule) is spelled out there. This ticket implements that doc.

**1. Dependency (`backend/pyproject.toml`):**
- `stripe==11.4.1` is **already** in `[project.dependencies]`. Verify it imports cleanly at `stripe.Customer`, `stripe.PaymentIntent`, `stripe.Webhook`, `stripe.Subscription` — no changes needed unless the version is broken. Do NOT upgrade unless there's a concrete reason.

**2. Config (`backend/app/config.py`):**
- Add new fields:
  - `BILLING_ENABLED: bool = False`
  - `BILLING_CURRENCY: Literal["usd"] = "usd"` — tighten via `typing.Literal` so non-USD values fail at Settings construction. Multi-currency is a future chassis extension.
  - `BILLING_TOPUP_MIN_MICROS: int = 500_000`  (= $0.50; Stripe's minimum chargeable amount)
  - `BILLING_TOPUP_MAX_MICROS: int = 500_000_000`  (= $500; chassis-generic upper bound for fraud reduction)
  - `STRIPE_TAX_ENABLED: bool = False`
  - `BILLING_SIGNUP_BONUS_MICROS: int = 0`  (off by default; projects opt in at deploy time)
  - `BILLING_VERIFY_BONUS_MICROS: int = 0`  (off by default)
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are **already declared** as `Optional[str] = None` at `config.py:129-130`. Keep them Optional; add validators (below) so they're required only when `BILLING_ENABLED=true`.
- Add two new `@model_validator(mode="after")` methods, matching the shape of the existing `validate_cors_origins` and `validate_cookie_domain`:
  - `validate_stripe_secret_key` — if `BILLING_ENABLED=True` and `STRIPE_SECRET_KEY` is empty/None, raise `ValueError` with remediation message.
  - `validate_stripe_webhook_secret` — same for `STRIPE_WEBHOOK_SECRET`.
  - Error-message style: quote the offending setting value, name exactly what to set, explain why (mirror CORS/cookie-domain messages — see `test_settings_validator.py` for the assertion shape).

**3. `backend/.env.example`:**
- `STRIPE_SECRET_KEY=` and `STRIPE_WEBHOOK_SECRET=` are **already present** at lines 55-56. Leave them.
- Add a new billing block: `BILLING_ENABLED=false`, `BILLING_CURRENCY=usd`, `BILLING_TOPUP_MIN_MICROS=500000`, `BILLING_TOPUP_MAX_MICROS=500000000`, `STRIPE_TAX_ENABLED=false`, `BILLING_SIGNUP_BONUS_MICROS=0`, `BILLING_VERIFY_BONUS_MICROS=0`. Group them together with a header comment `# Billing (chassis — optional)`.

**4. Alembic migration (`backend/alembic/versions/<timestamp>_0021_billing_foundation.py`):**
- `down_revision = "ee2ded47d8da"` (only existing migration — verified at audit time 2026-04-23).
- Add nullable `stripe_customer_id VARCHAR(64)` column to `users` table.
- Create `subscriptions`, `balance_ledger`, `stripe_events` tables — exact schema in `payments.md` §Data model. Match column types, nullability, defaults, and unique/plain indexes exactly. Partial unique index on `balance_ledger(stripe_event_id) WHERE stripe_event_id IS NOT NULL` is critical — it's the webhook idempotency guarantee.
- `downgrade()` drops in reverse order. Tables are empty if billing was never enabled; non-null `users.stripe_customer_id` values are lost on rollback, acceptable for chassis (prod ops would reconcile via Stripe Dashboard).
- Verify migration cycle: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` on an empty DB and on the current-main snapshot.

**5. Models (`backend/app/models/`):**
- Add `stripe_customer_id: Mapped[Optional[str]]` to the existing `User` model.
- Create `subscription.py`, `balance_ledger.py`, `stripe_event.py` models following the existing SQLAlchemy 2.0 async patterns in `refresh_token.py`.
- `balance_ledger.amount_micros` is `Mapped[int]` (bigint column). `BalanceLedger.stripe_event_id` is `Mapped[Optional[str]]` — NOT a ForeignKey (Stripe event IDs are not our primary keys; `stripe_events.id` is the authoritative store, but the ledger reference is informational + the unique index provides idempotency).

**6. Billing module (`backend/app/billing/`):**
- `__init__.py` exports the public API: `create_customer`, `get_balance_micros`, `grant`, `debit`, `format_balance`, `InsufficientBalanceError`, `Reason` enum.
- `exceptions.py` — `InsufficientBalanceError` (subclass of `Exception`; never `AppError` — the chassis primitive raises a typed exception, the HTTP layer translates if needed).
- `reason.py` — single `Reason` enum with all chassis-closed values: `TOPUP`, `SUBSCRIPTION_GRANT`, `SUBSCRIPTION_RESET`, `SIGNUP_BONUS`, `VERIFY_BONUS`, `DEBIT`, `REFUND`, `ADJUSTMENT`. Enum values are lowercase strings matching `payments.md` reason vocabulary.
- `format.py` — `format_balance(micros: int) -> str` per the display policy in `payments.md` §Display policy. Pure function; no I/O.
- `primitives.py` — the async functions (`create_customer`, `get_balance_micros`, `grant`, `debit`). All take `db: AsyncSession`. `grant` and `debit` use `db.execute()` / `db.add()`; they do NOT commit — the caller's transaction does.
- **`debit` concurrency safety — prescribed pattern:** inside the caller's transaction, acquire a row-level lock on the user before reading the balance, then insert the negative ledger entry:
  ```python
  await db.execute(select(User.id).where(User.id == user_id).with_for_update())
  balance = await _sum_ledger(user_id, db)
  if balance < amount_micros:
      raise InsufficientBalanceError(...)
  db.add(BalanceLedger(user_id=user_id, amount_micros=-amount_micros, reason=Reason.DEBIT.value, ref_type=ref_type, ref_id=ref_id))
  ```
  Rationale: serializes concurrent debits per user without requiring a denormalized balance column. Two debits arriving at the same moment block each other at the `SELECT ... FOR UPDATE`; the second one sees the first's ledger row and fails cleanly with `InsufficientBalanceError` if the balance is now too low. O(n) balance read per debit is acceptable at expected scale (hundreds of ledger entries per user); future denormalization is a separate optimization ticket if it becomes hot.
- `create_customer` uses Stripe idempotency key `f"register:{user.id}"` so a retried registration doesn't create duplicate Stripe Customers: `stripe.Customer.create(..., idempotency_key=f"register:{user.id}")`.
- `stripe_client.py` — small module that wraps the `stripe` SDK initialization (`stripe.api_key = settings.STRIPE_SECRET_KEY` lazy-init). Keeps the SDK integration surface narrow for monkey-patching in tests.

**7. Webhook route (`backend/app/routes/billing.py`):**
- New module. Router prefix `/billing`. Tag `billing`.
- Single endpoint for this ticket: `POST /billing/webhook`.
- Reads `request.body()` raw (not parsed JSON) + `stripe-signature` header.
- Verifies with `stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)`. Invalid signature → 400 with `UNAUTHORIZED` code (misuse, not auth-chain — but 400 is more accurate than 401 since the request didn't claim to be authenticated).
- Idempotency: check `stripe_events` for the event id inside a transaction; if already present, return 200 no-op.
- Dispatch: match on `event.type`. For 0021 scope, ALL event types land in the fallback branch that logs `"Unhandled Stripe event type: %s"` and returns 200. Specific handlers land in later tickets.
- Insert `stripe_events` row before returning 200 (records processing).
- Rate limit: none. Stripe IPs are known; spammy callers will fail signature verification. If we add rate limiting later it should be whitelist-based.
- **Endpoint is mounted conditionally.** In `backend/app/main.py`, only include the billing router when `settings.BILLING_ENABLED`. When disabled, the route returns 404 (Cloud Run behavior — unmounted route is invisible, not a maintained-but-disabled state).

**8. Auth integration (`backend/app/routes/auth.py`):**
- **Register hook** — slot right after `await db.flush()` at line 263 (where IntegrityError on duplicate email is handled), before the `create_verify_token` call at line 270. Wrap in a SQL **SAVEPOINT** (`async with db.begin_nested()`) so a billing failure rolls back only the billing changes without poisoning the outer txn:
  ```python
  if settings.BILLING_ENABLED:
      try:
          async with db.begin_nested():
              customer_id = await billing.create_customer(user)
              user.stripe_customer_id = customer_id
              if settings.BILLING_SIGNUP_BONUS_MICROS > 0:
                  await billing.grant(
                      user_id=user.id,
                      amount_micros=settings.BILLING_SIGNUP_BONUS_MICROS,
                      reason=billing.Reason.SIGNUP_BONUS,
                      db=db,
                  )
      except Exception:
          logger.exception("billing_register_hook_failed", extra={"user_id": user.id})
          # best-effort: register succeeds without Stripe linkage; future topup/subscribe lazy-creates the Customer.
  ```
- **Verify-email hook** — slot right after `user.verified_at = datetime.now(...)` at line 545, before the return. Same savepoint pattern:
  ```python
  if settings.BILLING_ENABLED and settings.BILLING_VERIFY_BONUS_MICROS > 0:
      try:
          async with db.begin_nested():
              await billing.grant(
                  user_id=user.id,
                  amount_micros=settings.BILLING_VERIFY_BONUS_MICROS,
                  reason=billing.Reason.VERIFY_BONUS,
                  db=db,
              )
      except Exception:
          logger.exception("billing_verify_hook_failed", extra={"user_id": user.id})
  ```
- Both integrations log-and-continue on failure; they never break the auth flow. A Stripe outage cannot block registration or email verification.
- When `BILLING_ENABLED=False` (default), both conditionals short-circuit and the `billing` module's Stripe-touching paths are never invoked. Import the module at the top of the file unconditionally (it's lightweight; lazy-import would complicate typing without real benefit).

**9. Chassis-contract update (`doc/operations/chassis-contract.md`):**
- Add two new `## Invariant:` sections, following the existing two entries' structure (required? / purpose / error message / enforcement location / how to satisfy):
  - `STRIPE_SECRET_KEY non-empty when BILLING_ENABLED=true`
  - `STRIPE_WEBHOOK_SECRET non-empty when BILLING_ENABLED=true`

**10. Backend API reference (`doc/reference/backend-api.md`):**
- Add the Billing section (already listed in the file but as a Stripe-placeholder). Document `POST /billing/webhook`: Stripe signature auth, 200 on success, 400 on invalid signature, idempotent.

**11. Tests — two files:**

**`backend/tests/test_settings_validator.py`** (extend existing): add two new test classes matching the existing `TestCorsOriginsValidator` / `TestCookieDomainValidator` pattern (kwargs-based Settings construction, ValidationError assertion):
- `TestStripeSecretValidator` — covers the `validate_stripe_secret_key` branches.
- `TestStripeWebhookSecretValidator` — covers `validate_stripe_webhook_secret`.

Map the Settings-level assertions from the list below into these classes. The remaining (non-Settings) tests go in the new file below.

**`backend/tests/test_billing_foundation.py`** (new file):
- **Balance / ledger primitives:**
  - `test_get_balance_zero_for_new_user` — fresh user → `get_balance_micros` returns 0.
  - `test_grant_increases_balance` — grant 1_000_000 → balance 1_000_000.
  - `test_debit_decreases_balance` — grant 1_000_000 → debit 400 → balance 999_600.
  - `test_debit_insufficient_balance_raises` — balance 100 → debit 200 → `InsufficientBalanceError`; balance unchanged.
  - `test_balance_sums_multiple_entries` — grant + grant + debit → balance reflects all three.
  - `test_ledger_stripe_event_id_unique_constraint` — insert two rows with same `stripe_event_id` → IntegrityError. (Validates the partial unique index.)
- **Format:**
  - `test_format_balance_zero` → `"$0.00"`.
  - `test_format_balance_whole_cents` → `"$1.23"` for 1_230_000 micros.
  - `test_format_balance_sub_cent` → `"$0.0034"` for 3_400 micros.
  - `test_format_balance_large` → `"$1000.00"` for 1_000_000_000 micros.
- **Settings validators:**
  - `test_settings_requires_stripe_secret_when_billing_enabled` — `BILLING_ENABLED=true` + unset `STRIPE_SECRET_KEY` → ValueError on Settings construction. Match pattern in existing `test_settings_validator.py`.
  - `test_settings_requires_stripe_webhook_secret_when_billing_enabled` — same.
  - `test_settings_allows_empty_stripe_keys_when_billing_disabled` — `BILLING_ENABLED=false` (default) + empty stripe keys → Settings constructs cleanly.
- **Auth integration:**
  - `test_register_does_not_create_customer_when_billing_disabled` — default config + register → `user.stripe_customer_id is None`, no Stripe SDK calls.
  - `test_register_creates_customer_when_billing_enabled` — `BILLING_ENABLED=true`, mock `stripe.Customer.create` → returns fake customer id → `user.stripe_customer_id` stored.
  - `test_register_grants_signup_bonus_when_configured` — `BILLING_ENABLED=true` + `BILLING_SIGNUP_BONUS_MICROS=1_000_000` → ledger has a `signup_bonus` row for 1_000_000 after register; balance reflects.
  - `test_register_skips_bonus_when_zero` — `BILLING_ENABLED=true` + default bonus=0 → no ledger entry.
  - `test_register_survives_stripe_failure` — `BILLING_ENABLED=true`, mock `stripe.Customer.create` to raise → register returns 200, user exists, `stripe_customer_id is None`, warning logged. Registration must NOT fail because Stripe is down.
  - `test_verify_email_grants_verify_bonus_when_configured` — similar shape.
- **Webhook:**
  - `test_webhook_rejects_invalid_signature` — POST with bad `stripe-signature` header → 400.
  - `test_webhook_accepts_valid_signature_unhandled_type` — valid signature for an event type the chassis doesn't handle yet → 200 + `stripe_events` row inserted + log.
  - `test_webhook_idempotent_on_replay` — same event id posted twice → both return 200 + only one `stripe_events` row + handler only invoked once.
  - `test_webhook_not_mounted_when_billing_disabled` — `BILLING_ENABLED=false` + POST /billing/webhook → 404.

  Stripe signature construction for valid-signature tests: use `stripe.WebhookSignature._compute_signature` or equivalent in the SDK's testing utilities. Check the Stripe SDK docs for the canonical pattern — don't hand-roll HMAC-SHA256 if the SDK exposes a helper.

**12. `cloudbuild.yaml` — no changes in this ticket.** `BILLING_ENABLED` default of `false` keeps staging deploys unchanged. Later tickets that flip it on add the env var (and the required Stripe keys) to cloudbuild.yaml.

### Phase 0b — frontend (frontend-builder)

No frontend changes. Not dispatched for this ticket.

### Phase 1 — user local verification

**Base (required before merge):**

1. `docker-compose up -d --build backend`
2. `docker-compose exec backend alembic upgrade head` → verify 3 new tables + 1 new column via `docker-compose exec db psql -U postgres -d postpass -c "\d users"` and `\dt`.
3. Register a new user via the existing frontend (`docker-compose up -d frontend` if not already running). Verify register still works unchanged; `user.stripe_customer_id` is `NULL` in DB.
4. Verify email flow still works unchanged. `verified_at` set; no new ledger entries (bonuses default to 0).

**Stretch (optional — exercises the Stripe integration in dev):**

5. Get a Stripe test-mode API key pair. Set in `backend/.env`:
   ```
   BILLING_ENABLED=true
   STRIPE_SECRET_KEY=sk_test_...
   STRIPE_WEBHOOK_SECRET=whsec_...   # from `stripe listen` output
   ```
6. Install Stripe CLI. Run `stripe listen --forward-to localhost:8000/billing/webhook` in a separate terminal. This prints `whsec_...`; copy it into `.env` as `STRIPE_WEBHOOK_SECRET`. Restart backend.
7. Register a new user. Confirm `user.stripe_customer_id` now populated; Stripe Dashboard (test mode) shows the new Customer with matching `metadata.user_id`.
8. Trigger a test event: `stripe trigger payment_intent.succeeded`. Backend logs:
   - `POST /billing/webhook` 200
   - `"Unhandled Stripe event type: payment_intent.succeeded"` warning
   - `stripe_events` table has a new row with the event id.
9. Re-run `stripe trigger payment_intent.succeeded` with the same event id (Stripe CLI reuses event IDs under some conditions; if not, retrigger). Confirm:
   - Second POST also returns 200.
   - `stripe_events` still has only one row for that id (partial unique index on `stripe_event_id` prevented duplicate).
10. Reset `backend/.env` to defaults before committing (or use a `.env.local` that's gitignored). Don't leak test keys to the repo.

### Phase 2 — staging

- Merge + push. Cloud Build redeploys with `BILLING_ENABLED=false` default (unchanged from main). No staging behavior changes, no Stripe keys needed yet. Future tickets (0022+) will add staging-level Stripe config when they need it.
- Confirm `alembic upgrade head` ran cleanly on staging via Cloud Build logs.
- Smoke-test auth still works on staging (existing smoke scripts).

## Verification

**Automated (backend-builder Phase 0a report):**
- `.venv/bin/pytest` green, including all new tests in `test_billing_foundation.py`.
- `ruff check .` + `ruff format --check .` clean.
- `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` cycle runs cleanly on an empty DB (migration reversibility sanity).
- Paste the new Settings fields inline.
- Paste the two new pydantic validators inline.
- Paste the `Reason` enum definition.
- Paste the signatures of the 5 public primitives (`create_customer`, `get_balance_micros`, `grant`, `debit`, `format_balance`).
- Paste the `POST /billing/webhook` route handler inline.
- Paste the register-hook diff and verify-email-hook diff.
- Paste the two new chassis-contract.md entries.
- Confirm `cloudbuild.yaml` is unchanged.
- Confirm no frontend file changed.

**Functional (user, Phase 1 base):**
- Migration up/down cycle observed in local DB.
- Register flow works; `user.stripe_customer_id` is NULL (billing-disabled default).
- Verify-email flow works; no spurious ledger entries.

**Functional (user, Phase 1 stretch — optional):**
- Register with `BILLING_ENABLED=true` creates Stripe Customer (visible in Dashboard test mode).
- Webhook receives event via Stripe CLI; idempotency holds on replay.

**Staging (user, Phase 2):**
- Cloud Build deploy clean; `alembic upgrade head` successful.
- Existing auth smoke tests still green.

## Chassis implications

With 0021 landed, the chassis gains its third subsystem (auth + email + billing). A future project adopting this chassis will:

1. Set `BILLING_ENABLED=true` + Stripe keys in their deploy.
2. Define subscription tiers in their Stripe Dashboard (Product + Price with `lookup_key`, `metadata.grant_micros`, `metadata.tier_name`).
3. Wire project-layer actions to call `billing.debit(...)`.
4. Add project-specific UI (pricing page, topup screen, balance display) using the chassis endpoints.

The chassis gains two new `chassis-contract.md` invariants. When `STARTER.md` is extracted (post-0016/0017 per `site-model.md` reusability constraint 7), the billing chassis contract inherits.

## Report

Backend-builder (Phase 0a):
- Files modified + one-line what-changed each.
- Pasted artifacts per Verification section.
- `pytest` + `ruff check` + `ruff format --check` summary lines.
- `alembic upgrade head / downgrade -1 / upgrade head` cycle outcome.
- Any deviation from the brief, with reasoning.

Orchestrator (on close):
- User Phase 1 base outcome.
- User Phase 1 stretch outcome (if run).
- User Phase 2 staging outcome.

## Resolution

*(filled in by orchestrator after user confirms Phase 1 base + Phase 2 staging pass; stretch verification is optional but captured if run)*
