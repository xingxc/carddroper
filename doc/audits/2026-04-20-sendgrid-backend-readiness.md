# SendGrid Backend Readiness Audit — 2026-04-20

**Ticket:** 0010 — SendGrid infrastructure  
**Auditor:** backend-builder (static analysis only — no code modified, no commands run that mutate state)  
**Scope:** Confirm every concrete claim in ticket 0010 against the current `backend/` tree before implementation is dispatched.

---

## 1. Ground-Truth Inventory

### 1.1 `email_service.py` — function signatures vs 0010 Pre-requisites table

Current file: `backend/app/services/email_service.py`

| Function | Current signature | Ticket table claim | Status |
|---|---|---|---|
| `send_email` | `def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool` | same | **CONFIRMED** |
| `send_verification_email` | `def send_verification_email(email: str, token: str, full_name: Optional[str] = None) -> bool` | same | **CONFIRMED** |
| `send_password_reset` | `def send_password_reset(email: str, token: str, full_name: Optional[str] = None) -> bool` | same | **CONFIRMED** |
| `send_email_change_verification` | `def send_email_change_verification(new_email: str, token: str, full_name: Optional[str] = None) -> bool` | same | **CONFIRMED** |
| `send_email_change_notification` | `def send_email_change_notification(old_email: str, new_email: str) -> bool` | same | **CONFIRMED** |

All 5 functions match the ticket's Pre-requisites table exactly. There is also a private `_button(url, label) -> str` helper not listed (correctly omitted — internal only).

### 1.2 Callsite line numbers and exact call shape

Current `backend/app/routes/auth.py` — verified by direct read:

| Ticket claim | Real line | Real call | Line drift? | Call matches? |
|---|---|---|---|---|
| L241 `register` | **L241** | `await asyncio.to_thread(send_verification_email, user.email, verify_token, user.full_name)` | **CONFIRMED** | **CONFIRMED** |
| L399 `forgot_password` | **L399** | `await asyncio.to_thread(send_password_reset, user.email, token, user.full_name)` | **CONFIRMED** | **CONFIRMED** |
| L494 `resend_verification` | **L494** | `await asyncio.to_thread(send_verification_email, current_user.email, token, current_user.full_name)` | **CONFIRMED** | **CONFIRMED** |
| L525-L527 `change_email` | **L525-L527** | `await asyncio.to_thread(send_email_change_verification, body.new_email, token, current_user.full_name)` | **CONFIRMED** | **CONFIRMED** |
| L569 `confirm_email_change` | **L569** | `await asyncio.to_thread(send_email_change_notification, old_email, new_email)` | **CONFIRMED** | **CONFIRMED** |

All 5 line numbers and call shapes match exactly.

### 1.3 `asyncio.to_thread(...)` — where does the offload live today?

The ticket states: "all currently wrap the send in `asyncio.to_thread(...)` at the outer helper level". This is confirmed: **every callsite in `routes/auth.py` wraps the helper function call in `asyncio.to_thread(...)`**. The email service functions themselves are synchronous (`def`, not `async def`). The offload is at the callsite, not inside `email_service.py`. The ticket's description is accurate.

### 1.4 Audit F-2 — no-key fallback at `email_service.py:20`

Ticket claim: logs `{"to": to, "body_text": text or html[:500]}`.

Actual code at lines 17-22:

```python
if not settings.SENDGRID_API_KEY:
    logger.info(
        "Email (dev — not sent, SENDGRID_API_KEY unset)",
        extra={"to": to, "subject": subject, "body_text": text or html[:500]},
    )
    return True
```

**CONFIRMED** with one addition: the actual log also includes `"subject": subject` — the ticket's F-2 description omits `subject` from what is logged but it is present. The `body_text` field (containing token URLs) is the security concern. `subject` is not sensitive. The F-2 fix (drop `body_text`, replace `to` with `to_hash`) remains correct and necessary.

### 1.5 `backend/requirements.txt` — existence and contents

File **exists** at `backend/requirements.txt`. Contents:

```
fastapi==0.115.0
uvicorn[standard]==0.34.0
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.1
pydantic[email]==2.10.0
pydantic-settings==2.7.0
python-jose[cryptography]==3.3.0
bcrypt==4.0.1
python-dotenv==1.0.1
stripe==11.4.1
httpx==0.28.1
python-multipart==0.0.20
slowapi==0.1.9
sendgrid==6.11.0
pytest==8.3.4
pytest-asyncio==0.25.2
```

Differences from `pyproject.toml`:
- `requirements.txt` includes `pytest` and `pytest-asyncio` (dev deps) in the same flat list — confirmed drift per F-4.
- `requirements.txt` does NOT include `ruff==0.9.0` (also a dev dep in pyproject.toml) — inconsistency within itself.
- Runtime deps are otherwise identical between the two files.

### 1.6 `backend/Dockerfile` — how Python deps are installed

Current Dockerfile installs via a **hand-maintained explicit `pip install` list**, NOT via `pip install -r requirements.txt` and NOT via `pip install .`. The relevant lines (9-25):

```dockerfile
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    "uvicorn[standard]==0.34.0" \
    "sqlalchemy[asyncio]==2.0.36" \
    asyncpg==0.30.0 \
    alembic==1.14.1 \
    "pydantic[email]==2.10.0" \
    pydantic-settings==2.7.0 \
    "python-jose[cryptography]==3.3.0" \
    bcrypt==4.0.1 \
    python-dotenv==1.0.1 \
    stripe==11.4.1 \
    httpx==0.28.1 \
    python-multipart==0.0.20 \
    slowapi==0.1.9 \
    sendgrid==6.11.0
```

`pyproject.toml` is copied but only serves as metadata — it is not used by pip to drive installs. F-4 claim is **CONFIRMED**: three hand-maintained lists must be kept in sync.

Note: the Dockerfile is single-stage (contrary to F-5's multi-stage suggestion), runs as root, and has no `HEALTHCHECK`. But those are F-5 concerns, not in scope for 0010. The ticket's F-4 fix (`pip install .`) is the correct remedy for 0010.

### 1.7 `backend/pyproject.toml` — `sendgrid`, `tenacity`, pydantic version

- `sendgrid==6.11.0` — **already present** in both `pyproject.toml` and Dockerfile. No install needed.
- `tenacity` — **NOT present** in `pyproject.toml`, `requirements.txt`, or Dockerfile. Must be added.
- Pydantic version: `pydantic[email]==2.10.0` and `pydantic-settings==2.7.0`. These are Pydantic v2. `SecretStr` is available in `pydantic` v2 as `from pydantic import SecretStr`. No import blocker.

### 1.8 `backend/app/config.py` — existing Settings fields

Current Settings fields related to email / SendGrid:

```python
SENDGRID_API_KEY: Optional[str] = None   # NOT SecretStr — plain Optional[str]
FROM_EMAIL: str = "noreply@carddroper.com"
FROM_NAME: str = "Carddroper"
```

**Not present** (to be added by 0010):
- `SENDGRID_SANDBOX: bool`
- `SENDGRID_TEMPLATE_VERIFY_EMAIL: str`
- `SENDGRID_TEMPLATE_RESET_PASSWORD: str`
- `SENDGRID_TEMPLATE_CHANGE_EMAIL: str`
- `SENDGRID_TEMPLATE_CREDITS_PURCHASED: str`

`SecretStr` is **NOT currently imported** anywhere in `config.py`. The file imports only `from pydantic import field_validator` and `from pydantic_settings import BaseSettings, SettingsConfigDict`. The implementing agent must add the `SecretStr` import.

`FROM_EMAIL` and `FROM_NAME` **already exist** — the implementing agent must NOT duplicate them, only change `SENDGRID_API_KEY` type from `Optional[str]` to `SecretStr` and add the new fields.

### 1.9 Lifespan wiring in `backend/app/main.py`

A FastAPI `lifespan` context manager **exists** at lines 21-44:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — testing database connection…")
    await init_db()
    # ... token cleanup ...
    yield
    logger.info("Shutting down")
```

The `init_email_client()` call can be added before the `yield`; `close_email_client()` after the `yield`. The pattern is already in place. **CONFIRMED** — no blocker here.

### 1.10 Runtime SA — `cloudbuild.yaml` step 4

The `cloudbuild.yaml` does NOT live at `backend/cloudbuild.yaml`. It lives at the repo root: `/Users/johnxing/mini/postapp/cloudbuild.yaml`. The ticket says "backend/cloudbuild.yaml" in its F-4 section but the path is actually the repo root.

Step 4 (`--service-account`):
```
--service-account=carddroper-runtime@carddroper-staging.iam.gserviceaccount.com
```

**CONFIRMED** — the SA name matches what the ticket assumes.

### 1.11 `cloudbuild.yaml` step 4 — current `--set-secrets` and `--set-env-vars`

Current step 4 `--set-secrets` (single entry, verbatim):
```
--set-secrets=DATABASE_URL=carddroper-database-url:latest,JWT_SECRET=carddroper-jwt-secret:latest
```

There is **NO** `--set-env-vars` argument on step 4 today.

The ticket's Phase 3 extension shape adds to `--set-secrets` and introduces a new `--set-env-vars` argument. The existing format uses comma-separated `NAME=secret-name:version` — the proposed extension matches this format. **No format conflict.**

`cloudbuild.yaml` is at the repo root, not under `backend/`. Phase 3's backend-builder brief must reference `/Users/johnxing/mini/postapp/cloudbuild.yaml`.

### 1.12 `backend/scripts/` directory

`backend/scripts/` directory does **NOT exist**. No `scripts/` subdirectory anywhere under `backend/`. The implementing agent must create it. There is no precedent for CLI entry point convention in this project.

The ticket specifies `scripts/smoke_email.py`. The Phase 4 smoke test uses `poetry run python scripts/smoke_email.py` (a problem — see Section 5). The correct invocation pattern given the venv setup would be `.venv/bin/python scripts/smoke_email.py --to=... --template=...` (run from the `backend/` directory) or `python -m scripts.smoke_email` if the file uses `if __name__ == "__main__"` and `backend/` is on `PYTHONPATH`. A plain `backend/scripts/smoke_email.py` with `if __name__ == "__main__"` run as `.venv/bin/python scripts/smoke_email.py` from `backend/` is the cleanest pattern consistent with this project.

### 1.13 `backend/app/logging.py` — structured JSON output

The `_JsonFormatter` at lines 13-35 serialises every log record to JSON via `json.dumps(log_obj)`. All keys passed in `extra={}` are emitted verbatim as top-level JSON fields (via the `record.__dict__` loop that captures anything not in the excluded set).

The ticket's desired log shape — `logger.info("email_sent", extra={"event": "email_sent", "template": ..., "to_hash": ..., ...})` — will produce well-formed JSON output because:
1. `record.getMessage()` returns the positional message string (`"email_sent"` → appears as `"message": "email_sent"`).
2. All `extra` keys are promoted to top-level JSON fields.

**CONFIRMED** — dropping a dict into `logger.info("...", extra={...})` will produce structured JSON. No change to the logger is needed. The ticket's assumed log shape is valid.

However, note: the `"event"` field in `extra` will appear alongside `"message"` (which would contain the same or similar string). The ticket shows `{"event":"email_sent",...}` as the canonical shape. The implementing agent should pass `extra={"event":"email_sent", ...}` and use a short `message` string (e.g., `logger.info("email_sent", extra={...})`). This will yield both `"message":"email_sent"` and `"event":"email_sent"` in the JSON — redundant but not harmful.

### 1.14 Test files that currently patch `email_service`

Grep across `backend/tests/` for any mock/patch/monkeypatch targeting `email_service` or the helper functions:

**Result: NONE.** No test file in `backend/tests/` patches or mocks `email_service`, `send_verification_email`, `send_password_reset`, `send_email_change_verification`, or `send_email_change_notification`.

How do the existing tests survive without patching? `conftest.py` sets `os.environ.setdefault("SENDGRID_API_KEY", "")` before the app imports. With an empty API key, `send_email` takes the dev-fallback path (logger.info, return True) and never makes a network call. All callsites catch `Exception` and swallow it. Tests pass because the email send is silently elided.

This means: **the existing test suite will still pass after the reshape** as long as the new `send_email` also returns without a network call when `SENDGRID_API_KEY` is empty (the ticket's deliverable #5 preserves this). No test patch targets need updating.

---

## 2. Findings

### DR-1 (medium) — `SENDGRID_API_KEY` type mismatch

**Ticket claim:** `SENDGRID_API_KEY: SecretStr = SecretStr("")`  
**Actual state:** `SENDGRID_API_KEY: Optional[str] = None`  
**Impact:** Two changes needed: (a) import `SecretStr` from pydantic, (b) change type and default. The existing dev-fallback check `if not settings.SENDGRID_API_KEY:` must also change to `if not settings.SENDGRID_API_KEY.get_secret_value():` because `SecretStr` is always truthy as an object. Every existing comparison must be updated.  
**Recommendation:** Brief the implementing agent explicitly about the `.get_secret_value()` unwrap needed in `email_service.py` and that `SecretStr("")` is always truthy.

### DR-2 (medium) — `cloudbuild.yaml` is at repo root, not `backend/`

**Ticket claim:** Phase 3 says "Edit the `--set-secrets` line on the backend Cloud Run deploy" — implies the agent knows where the file is. The 0009 audit says "backend/cloudbuild.yaml" in its inventory (F-4 section says "backend/Dockerfile" correctly but for cloudbuild, 0009 says nothing explicit).  
**Actual state:** `cloudbuild.yaml` is at `/Users/johnxing/mini/postapp/cloudbuild.yaml`, not under `backend/`.  
**Impact:** If the Phase 3 dispatch brief tells the agent to edit `backend/cloudbuild.yaml`, the agent will fail to find the file.  
**Recommendation:** The Phase 3 brief must specify the full path `/Users/johnxing/mini/postapp/cloudbuild.yaml`.

### DR-3 (medium) — `scripts/` directory does not exist; no invocation convention

**Ticket claim:** Deliverable 10 creates `backend/scripts/smoke_email.py`. Phase 4 invokes it with `poetry run python scripts/smoke_email.py`.  
**Actual state:** `backend/scripts/` does not exist; the project uses no poetry (venv at `backend/.venv/`).  
**Impact:** Agent must create the directory. Phase 4 instructions are broken (see DR-5).  
**Recommendation:** Brief states the agent must create `backend/scripts/__init__.py` (or leave it absent if running as a script, not a module) and that the correct invocation from `backend/` is `.venv/bin/python scripts/smoke_email.py`.

### DR-4 (low) — `subject` field also logged in F-2 dev fallback

**Ticket claim:** F-2 states the fallback logs `{"to": to, "body_text": text or html[:500]}`.  
**Actual state:** The fallback also logs `"subject": subject`. Subject strings like `"Verify your email — Carddroper"` are not sensitive, but they do confirm which operation triggered the email.  
**Impact:** Minor. The Phase 0 fix should also drop `subject` from the dev-fallback log (it references the message type, which is now covered by `template` in the new shape).  
**Recommendation:** In the brief, explicitly list all fields to drop: `to`, `subject`, `body_text`.

### DR-5 (low) — `ruff` absent from `requirements.txt`

**Ticket claim (F-4):** `requirements.txt` duplicates `pyproject.toml`.  
**Actual state:** `requirements.txt` has `pytest` and `pytest-asyncio` but is missing `ruff==0.9.0`, so it only partially duplicates the dev group.  
**Impact:** Not a blocker for 0010. The ticket's fix (delete `requirements.txt`) is still correct; just noting the file is already slightly inconsistent even before the fix.  
**Recommendation:** No change to the brief needed. The file gets deleted.

### DR-6 (nit) — `Dockerfile` copies `pyproject.toml` but never uses it

When the Dockerfile switches from the hand-maintained pip list to `pip install .`, the `COPY pyproject.toml ./` line is already present (line 9). The agent must change the `RUN pip install` block to `RUN pip install .` and also `COPY . .` already covers the full source. Confirm the agent knows to install in editable or non-editable mode. `pip install .` (non-editable) is appropriate for a container.  
**Recommendation:** Brief explicitly states `RUN pip install .` replaces the hand-maintained list; `COPY pyproject.toml ./` line remains; `COPY . .` must come before or after appropriately (currently `COPY . .` is at line 27, after the pip install — agent must move it before or use a two-step approach). See Gap G-1 below.

---

## 3. Gaps (Missing Specifics in 0010)

### G-1 — Dockerfile `COPY . .` ordering with `pip install .`

Currently: `COPY pyproject.toml ./` → `RUN pip install <list>` → `COPY . .`. This works because the pip install list is self-contained.

After switching to `pip install .`: pip needs the full source tree (at minimum `pyproject.toml`). For a non-editable install, `pip install .` reads `pyproject.toml` for metadata and installs the declared deps. The source files themselves are copied by the subsequent `COPY . .`. The question is whether `pip install .` in a bare directory with only `pyproject.toml` (no package directory) will succeed.

**Answer:** `pip install .` with only `pyproject.toml` present and no `src/` layout will install the declared deps from the `[project].dependencies` array (which is what we want). It will also attempt to install the package `carddroper-backend` itself, which requires a package directory. Since `app/` is not yet present at pip install time, it may fail or produce a warning.

**Recommendation for ticket:** Either (a) `COPY . .` before `RUN pip install .`, which breaks Docker layer caching (source changes rebuild all deps), or (b) use `pip install --no-build-isolation --no-deps . || true` after copying only `pyproject.toml` and then install deps separately with `pip install -r <(pip-compile pyproject.toml)`. The simplest correct fix is: move `COPY . .` before `RUN pip install .`. The brief should specify this ordering explicitly.

### G-2 — `dynamic_template_data` keys needed at each callsite

The ticket requires each callsite to pass `dynamic_template_data: dict` to the new `send_email(template=..., to=..., dynamic_template_data=...)`. The ticket does not specify what keys each template needs. The current helper functions construct this data internally:

- `send_verification_email`: has `full_name` (HTML-escaped), `url` (verify URL), `expiry_hours` (implicit from `EMAIL_VERIFY_EXPIRY_HOURS`).
- `send_password_reset`: has `full_name`, `url` (reset URL), `expiry_minutes`.
- `send_email_change_verification`: has `full_name`, `url` (confirm-email-change URL), `expiry_hours` (from `EMAIL_CHANGE_EXPIRY_HOURS`).
- `send_email_change_notification`: has `new_email` (the new address), no `full_name`.

When the 4 helpers are deleted, the callsites must inline the URL construction (currently e.g. `f"{settings.FRONTEND_URL}/verify-email?token={token}"`). The token is available at the callsite already. The implementing agent needs to move URL construction logic from the helpers into the callsite or into a small utility.

**Recommendation:** The brief should enumerate the expected `dynamic_template_data` dict for each template call, e.g.:
- VERIFY_EMAIL: `{"full_name": user.full_name or "", "verify_url": f"{settings.FRONTEND_URL}/verify-email?token={verify_token}"}`
- RESET_PASSWORD: `{"full_name": user.full_name or "", "reset_url": f"{settings.FRONTEND_URL}/reset-password?token={token}"}`
- CHANGE_EMAIL: `{"full_name": current_user.full_name or "", "confirm_url": f"{settings.FRONTEND_URL}/confirm-email-change?token={token}"}`
- EMAIL_CHANGE_NOTIFICATION (no template for this yet — see G-3)

### G-3 — `send_email_change_notification` maps to which `EmailTemplate`?

The ticket's `EmailTemplate` enum has four members: `VERIFY_EMAIL`, `RESET_PASSWORD`, `CHANGE_EMAIL`, `CREDITS_PURCHASED`. The `confirm_email_change` callsite (L569) calls `send_email_change_notification(old_email, new_email)` — a canary email to the OLD address.

The ticket does NOT specify which `EmailTemplate` covers this notification. `CHANGE_EMAIL` most naturally maps to the verification email sent to the NEW address. The canary to the OLD address is a separate email type with no enum member and no SendGrid template ID defined.

**Recommendation:** The ticket must be patched to either (a) add `EMAIL_CHANGE_NOTIFICATION` as a 5th enum member + corresponding Settings field, or (b) clarify that `CHANGE_EMAIL` covers both sends (verification to new + notification to old, using the same template). This is a missing spec that will cause the implementing agent to guess.

### G-4 — `full_name` availability at confirm_email_change callsite

`confirm_email_change` (L569) calls `send_email_change_notification(old_email, new_email)` with no `full_name`. The current `send_email_change_notification` function does not use `full_name`. After the reshape, if `dynamic_template_data` for the notification template needs `full_name`, the `confirm_email_change` endpoint must query it from the user object — which it does have (`user` is loaded at L549). But the ticket does not address this. If `full_name` is needed in the notification template, the brief must say so explicitly.

### G-5 — HTML escaping moves out of the service layer

The current helper functions call `html_module.escape(full_name)` before building the HTML. Since the new API passes `dynamic_template_data` to a server-side template, HTML escaping in Python is no longer needed (SendGrid handles it). The implementing agent should be told NOT to HTML-escape values in `dynamic_template_data`. If it copies the existing code, it will accidentally double-escape (SendGrid template escapes again).

---

## 4. Callsite Extras

Grep results for all email_service identifiers across `backend/`:

```
backend/app/services/email_service.py        — definitions (not callsites)
backend/app/routes/auth.py:52-56             — import block (4 helpers)
backend/app/routes/auth.py:241               — send_verification_email
backend/app/routes/auth.py:399               — send_password_reset
backend/app/routes/auth.py:494               — send_verification_email
backend/app/routes/auth.py:525-527           — send_email_change_verification
backend/app/routes/auth.py:569               — send_email_change_notification
```

**No callsite exists outside the 5-row table in 0010.** Every use is in `routes/auth.py`. The audit is complete.

No test file patches or imports `email_service`.

---

## 5. Ticket Copy-Edits

### CE-1 (blocker) — `poetry` used throughout; project has no poetry

Phase 4 smoke test:
```bash
SENDGRID_API_KEY= poetry run python scripts/smoke_email.py ...
```

And in the Verification section:
```bash
poetry run pytest tests/services/test_email_service.py -v
poetry run pytest
poetry run ruff check app/ tests/ scripts/
```

**Reality:** The project uses no poetry. The venv is at `backend/.venv/`. Correct invocations from `backend/`:

```bash
# Dry run
SENDGRID_API_KEY= .venv/bin/python scripts/smoke_email.py --to=<addr> --template=VERIFY_EMAIL

# Real send
SENDGRID_API_KEY="$(gcloud ...)" ... .venv/bin/python scripts/smoke_email.py --to=<addr> --template=VERIFY_EMAIL

# Tests
.venv/bin/pytest tests/services/test_email_service.py -v
.venv/bin/pytest

# Ruff
.venv/bin/ruff check app/ tests/ scripts/
```

All `poetry run` references in Phase 4 and Verification must be replaced.

### CE-2 (nit) — `backend/cloudbuild.yaml` path in Phase 3 brief

The Phase 3 brief does not specify the full path to `cloudbuild.yaml`. The file is at the **repo root** (`/Users/johnxing/mini/postapp/cloudbuild.yaml`), not under `backend/`. The Phase 3 brief should state this explicitly so backend-builder opens the right file.

### CE-3 (nit) — 0010 Pre-requisites says "inlined below" for audit extracts, but the 5-row callsite table matches actual state exactly

No copy-edit needed on the table itself — confirmed accurate. However, the phrase "Ground-truth callsites are in ticket 0009's audit report" followed by "this ticket does not require re-reading that file" is accurate — 0010 does inline everything needed.

### CE-4 (low) — Phase 4 smoke script path ambiguity

Phase 4 says `scripts/smoke_email.py` with no `backend/` prefix, but the command block starts with `cd /Users/johnxing/mini/postapp/backend`. This is consistent. However, since `backend/scripts/` does not exist yet, the brief to backend-builder (Phase 0) must explicitly say to create `backend/scripts/smoke_email.py` (with the `backend/` prefix relative to the repo root).

---

## 6. Risk Index

| Risk | If followed verbatim | Mitigation |
|---|---|---|
| `poetry run` in Phase 4 / Verification | Commands will fail with `poetry: command not found` | Replace all `poetry run` with `.venv/bin/` equivalents |
| `SecretStr` for `SENDGRID_API_KEY` changes truthiness | `if not settings.SENDGRID_API_KEY:` in the new email_service becomes always-False (SecretStr("") is truthy); key is never treated as absent | Explicitly tell the agent to use `settings.SENDGRID_API_KEY.get_secret_value()` for the empty check |
| `cloudbuild.yaml` path wrong in Phase 3 brief | Agent looks for `backend/cloudbuild.yaml`, file not found, agent either fails or creates a new file in the wrong place | Add full absolute path to Phase 3 dispatch brief |
| `dynamic_template_data` keys unspecified (G-2, G-3) | Agent invents key names that won't match actual SendGrid template variables when templates are built in Phase 1 | Define canonical key names per template before or during Phase 0 dispatch |
| `CHANGE_EMAIL` template ambiguity — canary email to old address has no enum member (G-3) | Agent picks an arbitrary template or skips the notification email | Add `EMAIL_CHANGE_NOTIFICATION` enum member and Settings field, or explicitly state `CHANGE_EMAIL` covers both |
| Dockerfile layer cache regression (G-1) | `pip install .` after `COPY pyproject.toml ./` only (without app source) may error during package installation | Specify `COPY . .` before `RUN pip install .` in the brief, accepting the cache tradeoff |
| `scripts/` directory missing | Agent may fail if it doesn't know to create the directory | Brief states: create `backend/scripts/` (and optionally `backend/scripts/__init__.py`) |
