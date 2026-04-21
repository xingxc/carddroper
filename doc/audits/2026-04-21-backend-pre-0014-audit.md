# Backend Pre-0014 Audit — 2026-04-21

**Scope:** `backend/app/**`, `backend/tests/**`, `backend/alembic/versions/**`,
`backend/pyproject.toml`, `backend/Dockerfile`, `backend/.dockerignore`,
`backend/scripts/`, `backend/.env.example`, `backend/tests/conftest.py`,
`cloudbuild.yaml` (backend-side concerns only).

**Context:** Pre-0014 checkpoint. Tickets 0009–0013 are all resolved. This audit
does not reopen any finding closed by 0009–0013. Cross-referenced against
`doc/issues/README.md` before assigning new finding numbers.

**Not audited:** `frontend/` (parallel frontend-builder pass).

---

## 1. Scope + Method

Files read manually (all of them — the codebase is small enough):

| Dir | Files | Approx lines |
|---|---|---|
| `app/` | 18 .py files | ~1 750 |
| `tests/` | 7 .py files | ~1 050 |
| `alembic/versions/` | 1 .py file | 67 |
| `scripts/` | 3 .py files | ~337 |
| Config / build | Dockerfile, .dockerignore, pyproject.toml, .env.example, cloudbuild.yaml | ~220 |
| **Total** | | **~3 420** |

Commands run:

| Command | Result |
|---|---|
| `.venv/bin/pytest tests/ -q` | **36 passed, 0 failed** — 2 warnings (see F-7 below) |
| `.venv/bin/ruff check .` | **Clean** — 0 errors, 0 warnings |
| `.venv/bin/ruff format --check .` | **1 file would be reformatted** (`alembic/versions/ee2ded47d8da_initial_schema.py`) — see F-1 |
| `.venv/bin/alembic heads` | `ee2ded47d8da (head)` — single head |
| `.venv/bin/alembic history` | `<base> -> ee2ded47d8da (head), initial schema` — single, contiguous |

---

## 2. Inventory

| Path | Purpose |
|---|---|
| `app/__init__.py` | Empty package marker |
| `app/base.py` | `DeclarativeBase` |
| `app/config.py` | `pydantic-settings` `Settings` singleton — all env vars |
| `app/database.py` | Async engine, `AsyncSessionLocal`, `get_db`, `init_db` |
| `app/dependencies.py` | `get_current_user_optional`, `get_current_user`, `require_verified`, `require_not_locked` |
| `app/errors.py` | `AppError` + factory functions; `app_error_handler` |
| `app/logging.py` | JSON structured logging; `LoggingMiddleware`; `get_logger` |
| `app/main.py` | FastAPI app; CORS; lifespan (token cleanup); global 500 handler |
| `app/models/__init__.py` | Registers all models for Alembic autogenerate |
| `app/models/user.py` | `User` ORM model |
| `app/models/refresh_token.py` | `RefreshToken` ORM model |
| `app/models/login_attempt.py` | `LoginAttempt` ORM model |
| `app/routes/auth.py` | All 13 auth endpoints; cookie helpers; Pydantic schemas |
| `app/services/auth_service.py` | JWT create/decode; bcrypt; refresh-token CRUD |
| `app/services/email_service.py` | `send_email`; SendGrid singleton client; sandbox + retry |
| `app/services/hibp.py` | k-anonymity HIBP check; `validate_password` |
| `app/services/lockout_service.py` | Per-account login lockout; isolated-session pattern |
| `alembic/versions/ee2ded47d8da_initial_schema.py` | Sole migration: `users`, `refresh_tokens`, `login_attempts` |
| `tests/conftest.py` | Test DB env; schema-reset `autouse` fixture; `client` fixture |
| `tests/test_auth_flow.py` | 11 e2e auth flow tests |
| `tests/test_exception_handler.py` | 5 tests for global 500 handler (ticket 0011) |
| `tests/test_jwt_claims.py` | 6 tests for JWT iss/aud (ticket 0011) |
| `tests/services/test_auth_service.py` | 3 bcrypt unit tests (ticket 0013 backfill) |
| `tests/services/test_email_service.py` | 11 email service unit tests (ticket 0013 backfill) |
| `scripts/smoke_healthz.py` | Staging smoke: `/health` |
| `scripts/smoke_auth.py` | Staging smoke: register → login → me → refresh → logout |
| `scripts/smoke_email.py` | Staging smoke: `send_email` against real SendGrid API |
| `Dockerfile` | Multi-stage (builder + runtime); non-root `appuser`; tini; HEALTHCHECK |
| `.dockerignore` | Excludes `.venv/`, `tests/`, `scripts/`, `.env*`, build caches |
| `pyproject.toml` | PEP 621; pinned runtime deps; dev group; ruff; pytest config |
| `.env.example` | Full template mirroring every `Settings` field |
| `cloudbuild.yaml` | Build + migrate + deploy (backend + frontend); staging only |

---

## 3. Findings

### F-1: Alembic migration file fails `ruff format` — CI would reject it
- **Severity:** medium
- **File:** `alembic/versions/ee2ded47d8da_initial_schema.py`
- **Description:** `ruff format --check .` reports this file would be reformatted. `ruff check .` passes clean. The migration has auto-generated long lines that ruff's formatter would reflow.
- **Why it matters:** If `ruff format --check` is ever added to CI (it is the obvious next step per `doc/operations/testing.md`'s "ruff clean" gate), every push will fail until this is fixed. Additionally it sets a precedent that Alembic-generated files are exempt from formatting, which means future migrations are also likely to fail the gate.
- **Remediation:** Inline small fix — run `.venv/bin/ruff format alembic/versions/ee2ded47d8da_initial_schema.py` once. No functional change. Alternatively add `alembic/` to `[tool.ruff] exclude` in `pyproject.toml` to permanently exempt generated migrations.
- **Estimated size:** trivial (1 command or 1 config line).

### F-2: `FRONTEND_URL` is a dead config field — duplicate of `FRONTEND_BASE_URL`
- **Severity:** medium
- **File:** `app/config.py:33`, `backend/.env.example:21`
- **Description:** `Settings` declares both `FRONTEND_URL: str = "http://localhost:3000"` and `FRONTEND_BASE_URL: str = "http://localhost:3000"`. All code that constructs email links (`auth.py` lines 244, 414, 520, 559) uses `settings.FRONTEND_BASE_URL`. `FRONTEND_URL` is never read anywhere in `app/`. `.env.example` documents both (line 21 and the implied default), so an operator may set `FRONTEND_URL` thinking it controls email links, only to discover that `FRONTEND_BASE_URL` is what matters.
- **Why it matters:** Operator configuration confusion. If staging has `FRONTEND_URL=https://staging.carddroper.com` but `FRONTEND_BASE_URL` is left at the default, every verification and reset email in staging silently points to `http://localhost:3000`. This is a silent misconfiguration rather than a loud failure.
- **Remediation:** Remove `FRONTEND_URL` from `Settings` and from `.env.example`, or rename `FRONTEND_BASE_URL` to `FRONTEND_URL` everywhere for consistency. Either direction; pick one. Open ticket 00XX scope: "remove dead `FRONTEND_URL` config field (1 line in config.py + .env.example)."
- **Estimated size:** XS (remove 2 lines; no migration needed).

### F-3: `users.updated_at` is never populated by application code — `onupdate=func.now()` is ineffective for async SQLAlchemy
- **Severity:** medium
- **File:** `app/models/user.py:26`, `alembic/versions/ee2ded47d8da_initial_schema.py:41`
- **Description:** `User.updated_at` is declared as `mapped_column(server_default=func.now(), onupdate=func.now())`. The `onupdate` keyword in SQLAlchemy async triggers a client-side `SET updated_at = now()` injection in `UPDATE` statements — but only when the ORM generates the UPDATE. In practice every field mutation in `auth.py` (password change, verify-email, reset-password, confirm-email-change) modifies `User` attributes directly and relies on the session's implicit flush. SQLAlchemy's `onupdate` does work in this pattern for synchronous Core `update()` statements; for async ORM attribute mutations it fires correctly — BUT the `func.now()` it injects calls `now()` on the **database server**, which uses the DB's local timezone (Postgres default is UTC in most managed configs, but is not guaranteed). The broader `doc/PLAN.md` convention document explicitly warns: _"Do not rely on `server_default=func.now()` for any column you'll later compare against a Python value — Postgres `now()` stores DB local time and will silently mismatch a UTC-naive filter."_ `updated_at` is documented as record-keeping only (never filtered), so the timezone mismatch risk is low — but the `onupdate` hook itself is fragile because bulk `db.execute(update(...))` statements (used in `revoke_all_user_tokens`) bypass ORM attribute tracking and will NOT fire `onupdate`. This means `users.updated_at` is silently stale whenever a bulk update touches related fields.
- **Why it matters:** Low urgency now (nobody queries `updated_at`), but it will bite when the next developer adds filtering or sorting by `updated_at`. The column appears trustworthy in the schema but is actually not reliably maintained.
- **Remediation:** Two options: (a) accept `updated_at` as best-effort server-side timestamp with no application guarantees (document it in the model); (b) write `updated_at = datetime.now(timezone.utc).replace(tzinfo=None)` explicitly in every application write path. Option (a) is sufficient for v0.1.0 since the field is purely informational. Open ticket 00XX scope: "document `users.updated_at` as server-side best-effort; add explicit writes if filtering is ever added."
- **Estimated size:** XS (doc comment) or S (add 4–5 explicit assignments).

### F-4: `require_not_locked` is defined but **not applied to any route** — the 7-day account lock described in `auth.md` is not enforced
- **Severity:** high
- **File:** `app/dependencies.py:114`, `app/routes/auth.py` (all routes)
- **Description:** `systems/auth.md` §Soft cap specifies: _"Day 7 onward (locked): a `require_not_locked` FastAPI dependency returns 403 on every route except `/auth/verify-email`, `/auth/resend-verification`, `/auth/change-email`, `/auth/me`, `/auth/logout`."_ The dependency exists and is correctly implemented (`dependencies.py:114`). However, it is imported nowhere in `app/routes/auth.py` and applied to no route. Any unverified user who is more than 7 days old can still call every endpoint without restriction.
- **Why it matters:** This is a designed security/UX control (prevent indefinitely-unverified accounts from taking paid actions and eventually delete them). Without enforcement, the 7-day lock does not exist at runtime. When billing endpoints land, `require_not_locked` must be applied to all non-exempt routes or unverified accounts can spend credits.
- **Remediation:** This is a blocker that should be resolved before billing. Two-step: (1) add `require_not_locked` to each route that should be locked (every auth route except the five exempt ones listed in the spec); (2) add tests for locked-account behaviour. Open ticket 00XX scope: "wire `require_not_locked` dependency to all applicable auth routes; add 2–3 tests for the 7-day lock path."
- **Estimated size:** S (7–10 route signatures to update + tests).

### F-5: `verify_email` endpoint uses `unauthorized` (401) for an already-used or mismatched token — should be 422 or 400
- **Severity:** low
- **File:** `app/routes/auth.py:482–487`
- **Description:** `POST /auth/verify-email` raises `unauthorized("Invalid or expired verification token.")` when the token fails to decode, and silently returns 200 `{"message": "Email already verified."}` when already verified. For the case where the token is structurally valid but the user is not found, it raises `unauthorized("Invalid verification token.")`. 401 is semantically wrong: the request has no authentication requirement and 401 implies the client should retry with credentials. The more appropriate status is 422 (token malformed) or 400 (token valid but already used). The `backend-api.md` catalogue lists 401, 403, 404, 409, 422, 429 as expected error codes for the auth surface — 401 on a public, unauthenticated endpoint is the wrong layer.
- **Why it matters:** Frontend clients that implement error handling for `/verify-email` will be surprised to see 401 and may redirect the user to a login page when they should be showing "invalid link" UI. This affects ticket 0014 directly since 0014 builds the frontend verification page.
- **Remediation:** Inline small fix: change `unauthorized(...)` to `validation_error(...)` (422) or `not_found(...)` (404) in `verify_email`. Same pattern applies to `reset_password` and `validate_reset_token` which also use `unauthorized` for token-decode failures on public endpoints.
- **Estimated size:** XS (3 call sites, same file).

### F-6: `cloudbuild.yaml` migrate step uses a hardcoded `sleep 3` to wait for the Cloud SQL proxy — fragile
- **Severity:** low
- **File:** `cloudbuild.yaml:37`
- **Description:** After launching the Cloud SQL proxy in the background, the migration step does `sleep 3` before running `alembic upgrade head`. If the proxy takes longer than 3 seconds to bind (e.g., under GCP load, first cold start), the migration step will fail with a connection error and the build will be retried or fail. The proxy has no explicit readiness signal in this shell invocation pattern.
- **Why it matters:** Intermittent migration failures cause Cloud Run to deploy the old image while the DB is already partially migrated, or the deploy is blocked. As migration count grows this becomes more likely.
- **Remediation:** Replace `sleep 3` with a poll loop: `until pg_isready -h 127.0.0.1 -p 5432 -q; do sleep 1; done` (requires `postgresql-client` in the image, or use a Python poll). Alternatively increase to `sleep 10` as a short-term band-aid. Open ticket 00XX scope: "replace `sleep 3` in cloudbuild migrate step with a poll-until-ready loop."
- **Estimated size:** XS (2-line shell change in cloudbuild.yaml).

### F-7: Two pytest warnings present — not failures, but noisy
- **Severity:** nit
- **File:** `tests/conftest.py`, `tests/test_exception_handler.py:117`
- **Description:** (a) pytest-asyncio `asyncio_default_fixture_loop_scope` is unset (pre-existing; carried from audit 0009/F-6, not yet resolved). (b) `test_exception_handler_registered_in_app` is a sync function marked `pytestmark = pytest.mark.asyncio` at module level — the asyncio mark is propagated to it, causing the warning `is marked with '@pytest.mark.asyncio' but it is not an async function`.
- **Why it matters:** Warning (b) is new since 0013; it comes from the test structure in `test_exception_handler.py`. Not a failure but will accumulate if pattern is repeated.
- **Remediation:** For (b): add `@pytest.mark.skip` or move the sync test out of the module-level `pytestmark` scope using a local mark override. For (a): add `asyncio_default_fixture_loop_scope = "function"` to `[tool.pytest.ini_options]`. Inline small fix.
- **Estimated size:** trivial.

---

## 4. Schema / Migration Drift

**Result: no drift.** The single migration `ee2ded47d8da` and the three SQLAlchemy models are in alignment:

| Table | Migration | Model | Match |
|---|---|---|---|
| `users` | id, email, password_hash, full_name, verified_at, token_version, stripe_customer_id, created_at, updated_at | Same columns, same types, same nullable flags | Yes |
| `refresh_tokens` | id, user_id (FK → users.id CASCADE), token_hash (UNIQUE), expires_at, revoked_at, created_at | Same | Yes |
| `login_attempts` | id (BIGINT), email, attempted_at (server_default now()), ip, success; composite index (email, attempted_at) | Same | Yes |

Alembic chain is single-headed and contiguous (`<base> -> ee2ded47d8da (head)`).

One cosmetic note: the migration file sets `ondelete='CASCADE'` for `refresh_tokens.user_id` via `ForeignKeyConstraint`, which matches the model's `ForeignKey("users.id", ondelete="CASCADE")`. Confirmed consistent.

---

## 5. Contract Drift vs Docs

### vs `doc/systems/auth.md`

| Spec requirement | Code state | Drift? |
|---|---|---|
| Access token: `{ sub, tv, exp, iss, aud }` | All five claims present in `create_access_token` | No |
| Purpose tokens: `iss` + `aud` | Present in `_create_purpose_token` | No |
| `verified_at` column | Present in model and migration | No |
| `POST /auth/register` sends verification email | Implemented, best-effort | No |
| `require_verified` dependency exists | Exists in `dependencies.py:108` | No |
| `require_not_locked` dependency exists | Exists in `dependencies.py:114` | No |
| `require_not_locked` **applied** to routes | **Not applied anywhere** (see F-4) | **YES — drift** |
| Per-IP rate limits configured | All 9 limits in `config.py` and `.env.example` | No |
| Per-account lockout: 10 failures / 15-min window | Implemented in `lockout_service.py` | No |
| Password min 10 chars | Enforced in `hibp.py:validate_password` | No |
| HIBP check fails open | Implemented in `hibp.py` | No |
| Refresh token: 7-day TTL, SHA-256 hash stored | Implemented in `auth_service.py` | No |
| Single-use by convention (no rotation on refresh) | Confirmed — `refresh()` issues new access token only | No |

### vs `doc/reference/backend-api.md`

All 13 auth endpoints listed in the catalogue are implemented. `/health` is present. `/billing`, `/credits`, `/subscriptions` are listed as planned but not yet implemented — this is correct given the phase status.

### vs `doc/systems/payments.md`

Payments are Phase 6 (not started). No drift to report — no billing code exists.

---

## 6. Pre-0014 Readiness

Ticket 0014 will build: email-verification signed-token backend flow + frontend verify/reset pages.

| Check | State |
|---|---|
| `create_verify_token(user_id, token_version)` exists | Yes — `auth_service.py:97` |
| `decode_verify_token(token)` exists | Yes — `auth_service.py:106` |
| `POST /auth/verify-email` endpoint exists | Yes — `auth.py:473` |
| `send_email(EmailTemplate.VERIFY_EMAIL, ...)` fully wired | Yes — called at register (`auth.py:240`) and resend (`auth.py:515`) |
| Email dev fallback logs `dev_preview_url` containing the token URL | Yes — `email_service.py:122` |
| `require_verified` dependency exists and can be imported | Yes |
| Verify token TTL: 24h per spec | Yes — `EMAIL_VERIFY_EXPIRY_HOURS = 24` |
| Token is single-use (gated by `verified_at` + `token_version` bump on success) | Yes — `auth.py:489–495` |
| `POST /auth/resend-verification` rate-limited | Yes — 3/hour |
| `FRONTEND_BASE_URL` used in verify link | Yes — confirmed correct |
| **`require_not_locked` applied to routes** | **No** — F-4 above |

**Verdict: CONDITIONAL GREEN-LIGHT.**

The verification backend flow itself is ready. The only pre-0014 blocker is F-4: `require_not_locked` is unapplied. If 0014 only lands the frontend verify/reset pages and does not add billing-gated routes, F-4 does not block 0014 from completing. However, if 0014 also wires any paid-action enforcement that relies on `require_not_locked` being active, it is a blocker.

Recommendation: file a companion ticket for F-4 and let it proceed in parallel or immediately before the first billing ticket.

---

## 7. Test Gaps (qualitative)

Beyond 0013 backfill:

- **`require_not_locked` is untested end-to-end** (no test creates a >7-day-old unverified user and attempts an endpoint). This will be naturally covered by the ticket that wires the dependency.
- **`resend_verification` happy path not tested via the HTTP layer.** The email is mocked/skipped (no SENDGRID_API_KEY), but the endpoint itself has no dedicated test asserting the 200 return and that it calls `send_email`.
- **`change_email` uniqueness constraint** (new email already exists → 409): not covered by an HTTP-layer test, only by code inspection.
- **`confirm_email_change` second uniqueness check** (race-condition guard at line 595–598): not tested.
- **`forgot_password` always-200 (anti-enumeration) with non-existent email**: not tested. A regression here would leak whether emails are registered.
- **`validate_reset_token` GET endpoint** (already-used token case): not tested.
- **Password-change double-submit prevention** (re-using the same reset token after password change → 401): covered by `test_password_reset_flow` but only for the replay path after reset, not after change-password.

---

## 8. Tech Debt That Compounds

1. **Duplicate frontend URL config field (`FRONTEND_URL` vs `FRONTEND_BASE_URL`)** — each new email template or redirect must now remember to use `FRONTEND_BASE_URL`. As templates multiply this will cause a silent misconfiguration bug on at least one staging deploy. Fix before adding Stripe redirect URLs.

2. **No `asyncio_default_fixture_loop_scope` in `pyproject.toml`** — pytest-asyncio will change the default in a future version. As tests multiply (0014, billing tests), the interaction between fixture scopes and event loop lifetimes becomes increasingly hazardous.

3. **Stripe package imported in `pyproject.toml` but unused** — not harmful today, but when Stripe work starts the absence of any import guard means a missing `STRIPE_SECRET_KEY` will only be discovered at call time, not at startup. Consider adding a startup check when Stripe code lands.

4. **`get_db` generator yields inside `try/except Exception` with a bare `raise`** — this is correct as written (rolls back on any exception), but the pattern means an AppError raised inside a route handler causes a rollback even for reads that did not mutate state. Benign now (reads are idempotent) but any future read-heavy endpoint that catches AppError internally would be surprised by an implicit rollback.

---

## 9. Dead Code

- **`FRONTEND_URL` setting** (config.py:33) — never read. See F-2.
- **`stripe` package** (pyproject.toml:22) — installed, never imported. Intentional placeholder.
- **`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`** (config.py:73–74) — never read. Intentional.
- **`CREDITS_PURCHASED` template field** (`SENDGRID_TEMPLATE_CREDITS_PURCHASED` in config.py:83, `.env.example:63`) — `EmailTemplate.CREDITS_PURCHASED` exists in `email_service.py:41` but is never called. Correct anticipation for Phase 6; acceptable.

No unexpected dead code found. All legitimate stubs are accounted for by the phase plan.

---

## 10. Open Items Deferred

- **`updated_at` best-effort accuracy** (F-3) — acceptable for v0.1.0; the column is informational only. Defer to a maintenance pass when the first filtering query on `updated_at` is added.
- **`asyncio_default_fixture_loop_scope` warning** (F-7a) — low urgency, but compound risk. Defer to next routine test maintenance pass; add `asyncio_default_fixture_loop_scope = "function"` to `pyproject.toml`.
- **Alembic migration ruff format** (F-1) — run once before the next migration is added to avoid confusion about which files need formatting.
- **cloudbuild.yaml `sleep 3` fragility** (F-6) — acceptable until the first intermittent migration failure is observed in staging. Keep on radar.
