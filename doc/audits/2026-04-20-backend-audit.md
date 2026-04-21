# Backend Audit — 2026-04-20

**Scope:** `backend/app/**`, `backend/tests/**`, `backend/alembic/**`, `backend/Dockerfile`, `docker-compose.yml`, `backend/.env.example`, `backend/pyproject.toml`

---

## 1. Inventory

| Path | Purpose |
|---|---|
| `app/__init__.py` | Empty package marker |
| `app/base.py` | SQLAlchemy `DeclarativeBase` definition |
| `app/config.py` | `pydantic-settings` `Settings` singleton; all env vars with defaults |
| `app/database.py` | Async engine, `AsyncSessionLocal`, `get_db` dependency, `init_db` ping |
| `app/dependencies.py` | `get_current_user`, `get_current_user_optional`, `require_verified`, `require_not_locked` |
| `app/errors.py` | `AppError` exception class + factory functions; `app_error_handler` |
| `app/logging.py` | JSON structured logging formatter, `LoggingMiddleware`, `get_logger` |
| `app/main.py` | FastAPI app init, CORS middleware, exception handlers, lifespan (token cleanup) |
| `app/models/__init__.py` | Registers all models for Alembic autogenerate |
| `app/models/user.py` | `User` ORM model |
| `app/models/refresh_token.py` | `RefreshToken` ORM model |
| `app/models/login_attempt.py` | `LoginAttempt` ORM model |
| `app/routes/auth.py` | All 13 auth endpoints; schemas; cookie helpers |
| `app/services/auth_service.py` | JWT creation/decode, bcrypt, refresh token CRUD |
| `app/services/email_service.py` | `send_email` (SendGrid / stdout dev fallback), 4 high-level send helpers |
| `app/services/hibp.py` | k-anonymity HIBP check; `validate_password` |
| `app/services/lockout_service.py` | Per-account login lockout; isolated session pattern for failed attempts |
| `alembic/versions/ee2ded47d8da_initial_schema.py` | Sole migration; creates `users`, `refresh_tokens`, `login_attempts` |
| `tests/conftest.py` | Test DB env setup, schema-reset autouse fixture, `client` fixture |
| `tests/test_auth_flow.py` | 11 end-to-end auth tests |
| `Dockerfile` | Single-stage Python 3.11-slim; `pip install` from pinned versions; `uvicorn` CMD |
| `docker-compose.yml` | Three services: `db` (Postgres 16), `backend`, `frontend` with healthchecks |
| `backend/.env.example` | Full env template mirroring every `Settings` field |
| `backend/pyproject.toml` | PEP 621 project; pinned runtime deps + dev group (pytest, ruff) |

---

## 2. Observable Checks

| Command | Result |
|---|---|
| `.venv/bin/pytest tests/` | **11 passed, 0 failed** — 1 deprecation warning (pytest-asyncio loop scope; see F-6) |
| `ruff check app/ tests/` | **Permission denied** — ruff binary inaccessible in this shell context; not run |
| `mypy app/` | Not configured; not run |
| `alembic heads` | **Permission denied** — alembic binary inaccessible; from filesystem inspection: one migration, `down_revision = None`, single head confirmed |
| `alembic check` | Not run |
| `docker compose config` | **Permission denied** — docker not accessible; compose file reviewed manually; no syntax issues observed |

---

## 3. Findings

### F-1: No global 500 exception handler — unhandled exceptions leak tracebacks
- **Severity:** high
- **Category:** security, bug
- **Location:** `backend/app/main.py` (repo-wide)
- **What:** `main.py` registers handlers only for `AppError` and `RateLimitExceeded`. Any unhandled `Exception` (e.g., DB connection drop, unexpected `None` dereference, asyncpg transport error) falls through to FastAPI's default handler, which returns a plain `{"detail": "Internal Server Error"}` with no `Content-Type: application/json` guarantee and no structured error code. Stack traces may appear in process stdout, which in Cloud Run are ingested by Cloud Logging and potentially visible in logs dashboards.
- **Why it matters:** Callers receive an inconsistent error shape, breaking any frontend that assumes `{"error": {"code": ...}}`. Stack traces in logs may include local variable values (e.g., DB URLs, partial SQL). On the security side, the absence of a sanitising 500 handler is a known OWASP information-disclosure vector.
- **Proposed follow-up:** New ticket — add `@app.exception_handler(Exception)` returning `{"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}}` with status 500 and request-id field.

### F-2: Dev-mode email logger emits recipient address as structured field
- **Severity:** medium
- **Category:** security
- **Location:** `backend/app/services/email_service.py:20`
- **What:** When `SENDGRID_API_KEY` is unset, `send_email` calls `logger.info(…, extra={"to": to, "subject": subject, "body_text": text or html[:500]})`. The `to` field is a real user email address. The JSON log formatter emits all `extra` keys verbatim. In a dev environment this goes to stdout, which is fine, but in a staging environment where `SENDGRID_API_KEY` is intentionally left unset (to avoid sending real mail), these logs contain PII and potentially token-bearing URLs (the `body_text` includes the full verification/reset link for the first 500 chars).
- **Why it matters:** Token-bearing URLs in logs violate OWASP A09 (security logging failures). Anyone with Cloud Logging read access on the staging project can harvest valid auth tokens.
- **Proposed follow-up:** Fold into ticket 0010 — truncate or omit `body_text`, replace `to` with a hash or masked form in the dev log path.

### F-3: JWT tokens have no `iss` or `aud` claims
- **Severity:** medium
- **Category:** security
- **Location:** `backend/app/services/auth_service.py:34` and `:43`
- **What:** Access tokens and all purpose tokens (`reset`, `verify`, `email_change`) are created with only `sub`, `tv`, `exp`, and optionally `purpose`. Neither `iss` (issuer) nor `aud` (audience) is set. The decoder in `dependencies.py:48` and `auth_service.py:54` does not pass `audience` or `issuer` to `jwt.decode`, so these are also not validated.
- **Why it matters:** Without `aud`, a token issued for one purpose (e.g., a verify token) is technically valid JWT syntax for an unrelated service that trusts the same `JWT_SECRET`. This is moot with a single service today but becomes relevant the moment a second service or webhook consumer is added. `iss` absence makes it impossible to detect tokens forged by a compromised secret from a different deployment.
- **Proposed follow-up:** New ticket — add `iss="carddroper"` and `aud="carddroper-api"` to all token payloads and validate both on decode.

### F-4: `requirements.txt` duplicates `pyproject.toml` and includes dev deps in prod list
- **Severity:** medium
- **Category:** dep-hygiene
- **Location:** `backend/requirements.txt`
- **What:** `requirements.txt` lists all runtime deps plus `pytest` and `pytest-asyncio`. This file appears to exist alongside `pyproject.toml` which correctly separates runtime from `[dependency-groups] dev`. The `Dockerfile` installs deps via an explicit `pip install` list that duplicates `pyproject.toml` a third time, not via `pip install -e .` or `--no-deps`. This means three places must be kept in sync manually.
- **Why it matters:** A dep added to `pyproject.toml` that is not mirrored to `requirements.txt` or the `Dockerfile` will be missing in production. `pytest` and `pytest-asyncio` in `requirements.txt` may be installed in production images if anyone uses that file.
- **Proposed follow-up:** New ticket — remove `requirements.txt` or make it a generated artifact (`pip-compile`); update `Dockerfile` to `pip install .` using `pyproject.toml`.

### F-5: Dockerfile runs as root, no HEALTHCHECK, single-stage build
- **Severity:** medium
- **Category:** security, dep-hygiene
- **Location:** `backend/Dockerfile`
- **What:** No `USER` directive — container runs as root. No `HEALTHCHECK` directive (compose file has one but the image itself does not, so standalone `docker run` has no health signal). Single-stage build: `gcc` and `libpq-dev` are installed but not dropped in a final slim layer, growing the image unnecessarily.
- **Why it matters:** Root container is a compliance failure for most cloud security policies; a container escape gives full host access. Missing `HEALTHCHECK` means Cloud Run cannot self-heal. Build tools in prod image are unnecessary attack surface.
- **Proposed follow-up:** New ticket — add `adduser`/`USER`, `HEALTHCHECK`, and a two-stage build (builder + final stage from `python:3.11-slim`).

### F-6: pytest-asyncio loop scope deprecation warning
- **Severity:** low
- **Category:** dep-hygiene
- **Location:** `backend/tests/conftest.py`
- **What:** pytest-asyncio 0.25.2 warns that `asyncio_default_fixture_loop_scope` is unset and will change default behaviour in a future version. Currently `_reset_schema` is `session`-scoped in terms of the event loop but `autouse=True` on every function; this interacts with the coming default. Tests still pass.
- **Why it matters:** Upgrade to pytest-asyncio 0.26+ without setting `asyncio_default_fixture_loop_scope = "function"` in `pyproject.toml` may cause fixture teardown ordering issues.
- **Proposed follow-up:** Defer — add `asyncio_default_fixture_loop_scope = "function"` to `[tool.pytest.ini_options]` in a routine maintenance pass.

### F-7: `bcrypt.gensalt()` uses library default rounds (12) — not explicit
- **Severity:** low
- **Category:** security
- **Location:** `backend/app/services/auth_service.py:21`
- **What:** `bcrypt.gensalt()` is called with no `rounds` argument, relying on bcrypt 4.x's default of 12. This is currently adequate but the value is not documented or configurable via settings.
- **Why it matters:** If the production machine is significantly faster than bcrypt's original calibration target, 12 rounds may under-protect against offline attacks. More practically: the rounds value is invisible in code review, making it easy to miss a future bcrypt library change that alters the default.
- **Proposed follow-up:** Defer — add a `BCRYPT_ROUNDS: int = 12` to `Settings` and thread it into `gensalt(rounds=settings.BCRYPT_ROUNDS)`.

### F-8: `stripe` package imported in `pyproject.toml` and `Dockerfile` but never called in code
- **Severity:** low
- **Category:** dead-code, dep-hygiene
- **Location:** `backend/pyproject.toml:14`, `backend/Dockerfile:21`
- **What:** `stripe==11.4.1` is a production dependency and is installed in the Docker image. No file under `app/` imports or uses it. The config carries `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` placeholder fields.
- **Why it matters:** Extra installed package adds attack surface and image size. Not a bug — Stripe is planned — but the import surface should stay zero until the feature is wired.
- **Proposed follow-up:** Fold into ticket 0012 (Stripe Customer on signup) — the dep is correct anticipation; just document in the ticket that Stripe import starts there.

### F-9: No `pytest-cov` configured; zero coverage visibility
- **Severity:** low
- **Category:** missing-test
- **Location:** `backend/pyproject.toml`
- **What:** `pytest-cov` is absent from dev dependencies. There is no `[tool.coverage.*]` section. Running pytest generates no coverage report.
- **Why it matters:** `email_service.py`, `hibp.py`, `lockout_service.py`, `logging.py`, and `dependencies.py` have no dedicated unit tests (all covered implicitly through the e2e auth flow). Zero isolated tests means any refactor of these modules will not be caught until the flow test breaks.
- **Proposed follow-up:** New ticket — add `pytest-cov` + `coverage.ini` + coverage gate at some reasonable threshold (e.g., 70%).

### F-10: `confirm-email-change` endpoint not protected by rate limit on the auth router — mismatched setting name
- **Severity:** nit
- **Category:** inconsistency
- **Location:** `backend/app/routes/auth.py:539`, `backend/app/config.py:54`
- **What:** The endpoint is decorated `@limiter.limit(settings.CONFIRM_EMAIL_CHANGE_RATE_LIMIT)` (10/minute) and the setting exists correctly. No real inconsistency — this is a clean check. Documenting for completeness.
- **Proposed follow-up:** Nothing needed.

---

## 4. Callouts for Upcoming Tickets

### Ticket 0010 (SendGrid hardening)

**`email_service.py` public API** (`backend/app/services/email_service.py`):

```python
def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool
def send_verification_email(email: str, token: str, full_name: Optional[str] = None) -> bool
def send_password_reset(email: str, token: str, full_name: Optional[str] = None) -> bool
def send_email_change_verification(new_email: str, token: str, full_name: Optional[str] = None) -> bool
def send_email_change_notification(old_email: str, new_email: str) -> bool
```

**All callsites** (all in `backend/app/routes/auth.py`):

| Line | Caller endpoint | Call |
|---|---|---|
| L241 | `register` | `await asyncio.to_thread(send_verification_email, user.email, verify_token, user.full_name)` |
| L399 | `forgot_password` | `await asyncio.to_thread(send_password_reset, user.email, token, user.full_name)` |
| L494 | `resend_verification` | `await asyncio.to_thread(send_verification_email, current_user.email, token, current_user.full_name)` |
| L525-L527 | `change_email` | `await asyncio.to_thread(send_email_change_verification, body.new_email, token, current_user.full_name)` |
| L569 | `confirm_email_change` | `await asyncio.to_thread(send_email_change_notification, old_email, new_email)` |

**Pattern:** All callsites use `asyncio.to_thread(...)` (correct — the SendGrid SDK is sync). All are wrapped in `try/except Exception` with `logger.error`. Email failures are best-effort; they do not abort the request or roll back the DB write.

**Settings fields related to email** (all in `backend/app/config.py`):

```python
SENDGRID_API_KEY: Optional[str] = None   # None = dev stdout fallback
FROM_EMAIL: str = "noreply@carddroper.com"
FROM_NAME: str = "Carddroper"
```

**PII / token-in-log issue:** `send_email` at line 20 logs `{"to": to, "subject": subject, "body_text": text or html[:500]}` in dev mode. The `body_text` value for verification and reset emails contains the full token URL.

### Ticket 0011 (Email verification polish)

**Register flow re: email:**
- `POST /auth/register` → creates user → calls `send_verification_email` best-effort (never blocks or returns error on send failure). Returns 200 + full `AuthResponse` regardless.
- `POST /auth/verify-email` (`body.token`) — decodes JWT verify token, checks `user.token_version == payload["tv"]`, sets `user.verified_at = utc_naive_now()`, bumps `token_version`, revokes all refresh tokens. Returns `{"message": "Email verified."}`.
- `POST /auth/resend-verification` — requires authenticated user (access token), checks `verified_at is None`, mints a new verify token using current `token_version`, sends email best-effort.

**Verify token storage:** Verify tokens are **stateless JWTs** (no DB table). Expiry is encoded in the JWT `exp` claim (`EMAIL_VERIFY_EXPIRY_HOURS = 24`). Single-use is enforced by `token_version`: verifying bumps the version, making the same JWT invalid on re-use. No replay window beyond the version bump.

**Re: `verified_at` is None check in resend:** Works correctly. Already-verified users get `{"message": "Email already verified."}` without re-sending.

### Ticket 0012 (Stripe Customer on signup)

**Register flow insertion point:** `backend/app/routes/auth.py:225–251`. After `await db.flush()` succeeds (user has a PK at `user.id`), before `create_access_token`. That is the correct insertion point for a `stripe.Customer.create` call — user ID is available, DB write has not yet committed.

**`users.stripe_customer_id` current state:**
- Model: `stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)` — nullable, no index, no unique constraint.
- Migration (`ee2ded47d8da`): column present (`sa.Column('stripe_customer_id', sa.String(length=64), nullable=True)`).
- No code reads or writes this column today.

### Ticket 0013 (Stripe webhook)

No webhook handler exists. `STRIPE_WEBHOOK_SECRET` is in `Settings` as `Optional[str] = None`. The `stripe` package is installed. Starting point: add `POST /billing/webhook` route with raw body access and signature verification via `stripe.Webhook.construct_event`.
