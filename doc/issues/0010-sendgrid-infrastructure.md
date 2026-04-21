---
id: 0010
title: SendGrid infrastructure — hardened send_email() helper + staging secret wiring
status: resolved
priority: high
found_by: orchestrator 2026-04-20
---

## Context

PLAN.md §10.5 (email layer) and §10.6 (Stripe) both consume this service. This
ticket lands a production-grade email helper *before* either layer calls it,
so we never retrofit retries / templating / async-offload across live
callsites.

The scaffold left behind `backend/app/services/email_service.py` (and it is
**already consumed** by `backend/app/routes/auth.py` — register, verify-resend,
forgot-password, change-email). Ground-truth callsites are in ticket 0009's
audit report — this ticket reshapes the helper *and* updates every existing
callsite to the new signature.

**Readiness audit (2026-04-20, patch 2026-04-21).** Three readiness audits
(`doc/audits/2026-04-20-sendgrid-doc-readiness.md`,
`doc/audits/2026-04-20-sendgrid-backend-readiness.md`,
`doc/audits/2026-04-20-sendgrid-frontend-readiness.md`) ran before
implementation dispatch. This ticket was patched 2026-04-21 with their
findings: 5th canary template (`EMAIL_CHANGED`) added because the
`auth.md` canary is a distinct email from the change-email verification link;
`dynamic_template_data` key contract specified per template;
`FRONTEND_BASE_URL` added to Settings; best-effort callsite semantics made
explicit; `SecretStr` truthiness unwrap specified; Dockerfile `COPY .`
ordering specified; `poetry` references swapped for `.venv/bin`;
`cloudbuild.yaml` repo-root path named; HTML-escaping dropped;
`dev_preview_url` added to the no-key fallback.

**Improvements over the foodapp pattern** (all to be delivered):

1. **Singleton SendGrid client.** Instantiate once at FastAPI startup (`main.py` lifespan), reuse across requests.
2. **Async offload for the sync SDK.** `sendgrid.SendGridAPIClient` is blocking; all calls route through `asyncio.to_thread(...)`. Without this, every send blocks the event loop.
3. **Per-attempt HTTP timeout of 5 seconds.**
4. **Tenacity retry with exponential backoff.** 3 attempts, 1s → 4s → 16s. Retry on `requests.Timeout`, `requests.ConnectionError`, or any exception with `status_code` in `{429, 500, 502, 503, 504}`. Other 4xx fail loud.
5. **Dynamic Templates.** Every outbound email uses a server-side template ID + `dynamic_template_data: dict`. No hardcoded HTML.
6. **Typed `EmailTemplate` enum** (5 members, see Phase 0 deliverable 1).
7. **Structured logging.** `{event, template, to_hash, sg_message_id, attempt}` — `to_hash` is SHA-256 of the lowercased email. PII-safe.
8. **Sandbox mode** via `SENDGRID_SANDBOX`. When true, SendGrid validates but does not deliver.
9. **`from_address` / `from_name` as parameters** (defaults from `FROM_EMAIL` / `FROM_NAME`). Forward-compat for v0.2 multi-tenant.
10. **`SecretStr` for API key** so logs / `/health?debug=true` never leak the raw key.

## Pre-requisites

- Ticket 0008 resolved (staging custom domains live).
- Ticket 0009 (code audit) resolved (2026-04-20).
- Access to the Carddroper SendGrid account (user must create if new).

**Current `email_service.py` public API** (to be fully reshaped in Phase 0):

```python
def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool
def send_verification_email(email: str, token: str, full_name: Optional[str] = None) -> bool
def send_password_reset(email: str, token: str, full_name: Optional[str] = None) -> bool
def send_email_change_verification(new_email: str, token: str, full_name: Optional[str] = None) -> bool
def send_email_change_notification(old_email: str, new_email: str) -> bool
```

**All callsites** (all in `backend/app/routes/auth.py`, all currently wrap the
send in `asyncio.to_thread(...)` at the outer helper level — Phase 0 moves the
offload *inside* `send_email` so callsites just `await send_email(...)`):

| Line | Endpoint | Current call |
|---|---|---|
| L241 | `register` | `send_verification_email(user.email, verify_token, user.full_name)` |
| L399 | `forgot_password` | `send_password_reset(user.email, token, user.full_name)` |
| L494 | `resend_verification` | `send_verification_email(current_user.email, token, current_user.full_name)` |
| L525-L527 | `change_email` | `send_email_change_verification(body.new_email, token, current_user.full_name)` |
| L569 | `confirm_email_change` | `send_email_change_notification(old_email, new_email)` |

**Audit-derived fixes folded into Phase 0:**

- **F-2 (medium, security):** the current no-key dev fallback at `email_service.py:20` logs `{"to": to, "subject": subject, "body_text": text or html[:500]}` — `body_text` contains the full verification/reset link (valid auth token) in production-shape logs. Phase 0 must drop all three (`to`, `subject`, `body_text`) from the fallback log. Safe fields: `event`, `template`, `to_hash`, `mock_message_id`, `dev_preview_url` (single preview URL only, safe because this path only fires in dev after 0010 lands — staging/prod always have SENDGRID_API_KEY set via Secret Manager).

- **F-4 (medium, dep-hygiene):** `backend/requirements.txt` duplicates `pyproject.toml` and the `Dockerfile` installs via a third hand-maintained `pip install` list. Phase 0 deletes `backend/requirements.txt` and updates the `Dockerfile` to `pip install .` against `pyproject.toml`. **Layer ordering matters: `COPY . .` MUST precede `RUN pip install .`** — pip needs the package source (`app/` directory + `pyproject.toml`) present to install. Current Dockerfile has `COPY . .` AFTER the pip install; move it before. tenacity gets added to `pyproject.toml` (not anywhere else).

**Confirm staging still green:**

```bash
curl -sSf https://api.staging.carddroper.com/health
# {"status":"ok","database":"connected"}
```

## Acceptance

### Phase 0: backend-builder — reshape `email_service.py` + update all callsites (agent-executed)

Orchestrator dispatches **backend-builder** with this brief:

```
Task: Reshape backend/app/services/email_service.py to the production-grade helper
  described in ticket 0010, and update every existing callsite to the new signature.

Ground truth: the Pre-requisites section of this ticket inlines the current
  email_service public API (5 functions) and all 5 callsites in routes/auth.py
  (L241, L399, L494, L525-L527, L569). Every callsite in that table switches
  to the new send_email signature. The four helper functions
  (send_verification_email, send_password_reset, send_email_change_verification,
  send_email_change_notification) are DELETED.

  If you find an email_service callsite outside that table, report it and still
  update it.

====================================================================
Deliverable 1 — Public API (5-member enum)
====================================================================

    class EmailTemplate(str, Enum):
        VERIFY_EMAIL = "VERIFY_EMAIL"              # → verification link to the signup address
        RESET_PASSWORD = "RESET_PASSWORD"          # → reset link to the account address
        CHANGE_EMAIL = "CHANGE_EMAIL"              # → verification link to the NEW address
        EMAIL_CHANGED = "EMAIL_CHANGED"            # → canary notification to the OLD address
        CREDITS_PURCHASED = "CREDITS_PURCHASED"    # → Stripe receipt (consumer ticket; Phase 0 does NOT wire this)

    async def send_email(
        *,
        template: EmailTemplate,
        to: str,
        dynamic_template_data: dict,
        from_address: str | None = None,
        from_name: str | None = None,
    ) -> str  # returns the SendGrid x-message-id on success, or "local-<uuid>" on no-key fallback

The 5th member (EMAIL_CHANGED) is NEW vs the scaffold. The auth.md §Email change
canary — "your email was changed; contact support if not you" — is a distinct
email from the change-email verification link, so it gets its own template.

====================================================================
Deliverable 2 — dynamic_template_data KEY CONTRACT
====================================================================

Exactly these keys per template. Phase 1 (user) will declare these variable
names inside the SendGrid Dynamic Template designer, so agreement is critical.

  VERIFY_EMAIL:
    - verify_url:  str    # f"{settings.FRONTEND_BASE_URL}/verify-email?token={jwt}"
    - full_name:   str | None  # Optional on User; template must render a fallback like "Hi there," when None

  RESET_PASSWORD:
    - reset_url:   str    # f"{settings.FRONTEND_BASE_URL}/reset-password?token={jwt}"
    - full_name:   str | None

  CHANGE_EMAIL (verification to NEW address):
    - change_url:  str    # f"{settings.FRONTEND_BASE_URL}/confirm-email-change?token={jwt}"
    - full_name:   str | None
    - new_email:   str    # destination address; useful context in the template body

  EMAIL_CHANGED (canary notification to OLD address; no clickable token):
    - old_email:       str
    - new_email:       str
    - change_date:     str      # ISO-8601 UTC, e.g. datetime.now(timezone.utc).isoformat()
    - support_email:   str = "support@carddroper.com"   # hard-coded constant; MX routing deferred per PLAN.md §11

  CREDITS_PURCHASED:
    - DEFERRED to Stripe receipt ticket. Phase 0 does NOT wire this. Leave the
      enum member + Settings field + Secret Manager entry in place.

Values MUST NOT be HTML-escaped on the Python side. SendGrid Dynamic Templates
escape automatically; double-escape produces literal `&amp;` etc. in rendered
emails. Drop the current `html.escape(full_name)` in helpers.

====================================================================
Deliverable 3 — Singleton SendGrid client + lifespan wiring
====================================================================

Expose `init_email_client()` / `close_email_client()` (close may be a no-op).
Wire into main.py's existing FastAPI lifespan (the `@asynccontextmanager` at
main.py lines 21-44). `init_email_client()` runs before `yield`, close after.

====================================================================
Deliverable 4 — Async offload + per-attempt timeout
====================================================================

ALL outbound calls go through `asyncio.to_thread(client.send, mail)`. The SDK
is sync; calling it on the event loop is a bug. Per-attempt HTTP timeout =
5.0s, set on the client's requests session.

====================================================================
Deliverable 5 — Tenacity retry
====================================================================

- 3 attempts, exponential wait 1s → 4s → 16s.
- Retry on: requests.Timeout, requests.ConnectionError, OR any exception
  where getattr(exc, "status_code", None) in {429, 500, 502, 503, 504}.
- Do NOT retry on other 4xx.
- Each attempt emits a structured log line with `attempt` incremented.

====================================================================
Deliverable 6 — No-key fallback
====================================================================

If the API key is empty, skip the network call and return "local-<uuid4>".

IMPORTANT: SENDGRID_API_KEY is now `SecretStr`, not `Optional[str]`. A bare
`SecretStr("")` is TRUTHY AS AN OBJECT — `if not settings.SENDGRID_API_KEY:` will
NEVER fire. Correct check:

    if not settings.SENDGRID_API_KEY.get_secret_value():
        <no-key fallback>

Fallback log shape (absolutely nothing else):

    logger.info("email_skipped_no_key", extra={
        "event": "email_skipped_no_key",
        "template": <EmailTemplate.name>,
        "to_hash": <sha256 hex of lowercased to>,
        "mock_message_id": f"local-{uuid4()}",
        "dev_preview_url": <verify_url | reset_url | change_url | None>,
            # whichever URL is in dynamic_template_data; None for EMAIL_CHANGED
    })

Do NOT log: `to`, `subject`, `body_text`, `body_html`, `full_name`, or any
dynamic_template_data key other than the single preview URL.

`dev_preview_url` is safe here because after 0010 this fallback only fires in
local dev (staging/prod always have SENDGRID_API_KEY set). F-2's security
concern is moot in that state.

====================================================================
Deliverable 7 — Sandbox mode
====================================================================

Settings.SENDGRID_SANDBOX (bool, default False). When True, set
`mail.mail_settings.sandbox_mode.enable = True`. Network call still happens;
SendGrid returns 200 without delivering.

====================================================================
Deliverable 8 — Settings additions (backend/app/config.py)
====================================================================

    SENDGRID_API_KEY: SecretStr = SecretStr("")
    SENDGRID_SANDBOX: bool = False
    SENDGRID_TEMPLATE_VERIFY_EMAIL: str = ""
    SENDGRID_TEMPLATE_RESET_PASSWORD: str = ""
    SENDGRID_TEMPLATE_CHANGE_EMAIL: str = ""
    SENDGRID_TEMPLATE_EMAIL_CHANGED: str = ""          # 5th — canary to old address
    SENDGRID_TEMPLATE_CREDITS_PURCHASED: str = ""
    FRONTEND_BASE_URL: str = "http://localhost:3000"   # staging overrides via --set-env-vars

DO NOT re-declare FROM_EMAIL or FROM_NAME — they already exist in config.py.
Only CHANGE SENDGRID_API_KEY type (Optional[str] → SecretStr) and ADD the new
fields. Import `from pydantic import SecretStr` at the top of config.py.

EmailTemplate → settings-field resolution via a module-level dict, e.g.:

    _TEMPLATE_FIELD = {
        EmailTemplate.VERIFY_EMAIL:        "SENDGRID_TEMPLATE_VERIFY_EMAIL",
        EmailTemplate.RESET_PASSWORD:      "SENDGRID_TEMPLATE_RESET_PASSWORD",
        EmailTemplate.CHANGE_EMAIL:        "SENDGRID_TEMPLATE_CHANGE_EMAIL",
        EmailTemplate.EMAIL_CHANGED:       "SENDGRID_TEMPLATE_EMAIL_CHANGED",
        EmailTemplate.CREDITS_PURCHASED:   "SENDGRID_TEMPLATE_CREDITS_PURCHASED",
    }

If the resolved template ID is empty at send time, raise ValueError
("SENDGRID_TEMPLATE_<NAME> is not configured"). Fail loud on misconfig.

====================================================================
Deliverable 9 — Structured logging
====================================================================

Use the existing app logger. The JSON formatter at app/logging.py promotes all
`extra` keys to top-level fields. Shapes:

    INFO:  logger.info("email_sent", extra={
             "event": "email_sent", "template": "VERIFY_EMAIL", "to_hash": "<hex>",
             "sg_message_id": "<x-message-id>", "attempt": 1,
           })
    ERROR: logger.error("email_send_failed", extra={
             "event": "email_send_failed", "template": "...", "to_hash": "...",
             "status_code": <int or None>, "attempt": <int>, "error": "<exc class>",
           })

====================================================================
Deliverable 10 — backend/.env.example
====================================================================

Update to include all new fields with safe defaults:
    SENDGRID_API_KEY=
    SENDGRID_SANDBOX=false
    SENDGRID_TEMPLATE_VERIFY_EMAIL=
    SENDGRID_TEMPLATE_RESET_PASSWORD=
    SENDGRID_TEMPLATE_CHANGE_EMAIL=
    SENDGRID_TEMPLATE_EMAIL_CHANGED=
    SENDGRID_TEMPLATE_CREDITS_PURCHASED=
    FROM_EMAIL=noreply@carddroper.com
    FROM_NAME=Carddroper
    FRONTEND_BASE_URL=http://localhost:3000

====================================================================
Deliverable 11 — scripts/smoke_email.py
====================================================================

Create `backend/scripts/` directory (does NOT exist today). Inside it create
`smoke_email.py`: a tiny CLI that takes --to=<addr> and --template=<name>,
constructs a stub dynamic_template_data dict matching the template, and calls
`await send_email(...)`. Reads config from env (same Settings as the app). Prints
the returned sg_message_id (or "local-<uuid>") and exits 0 on success.

Invocation convention (from `backend/`):
    .venv/bin/python scripts/smoke_email.py --to=foo@bar.com --template=VERIFY_EMAIL

No __init__.py needed if run as a script.

====================================================================
Deliverable 12 — Update all 5 callsites in routes/auth.py
====================================================================

Delete the 4 helper wrappers in email_service.py
(send_verification_email, send_password_reset, send_email_change_verification,
send_email_change_notification). Templates are server-side; HTML construction
moves out of Python; URL construction moves to the callsite.

**CRITICAL: preserve best-effort semantic.** Each callsite MUST wrap
`await send_email(...)` in its own `try/except Exception`, log via
`logger.exception(...)`, and continue. Email-send failure must NOT fail the
enclosing HTTP request. This is current behaviour per the 0009 audit and must
not regress. Example:

    try:
        await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to=user.email,
            dynamic_template_data={
                "verify_url": f"{settings.FRONTEND_BASE_URL}/verify-email?token={verify_token}",
                "full_name": user.full_name,
            },
        )
    except Exception:
        logger.exception("verification_email_send_failed", extra={"user_id": user.id})
        # do NOT raise — registration succeeded; email is best-effort.

For the L569 `confirm_email_change` callsite (canary to OLD address):

    try:
        await send_email(
            template=EmailTemplate.EMAIL_CHANGED,
            to=old_email,
            dynamic_template_data={
                "old_email": old_email,
                "new_email": new_email,
                "change_date": datetime.now(timezone.utc).isoformat(),
                "support_email": "support@carddroper.com",
            },
        )
    except Exception:
        logger.exception("email_changed_canary_send_failed", extra={"user_id": user.id})

The existing current-scaffold callsite does not use full_name for this
notification; do not add it unless Phase 1 template design calls for it.

====================================================================
Deliverable 13 — Dep-hygiene (audit F-4)
====================================================================

Delete `backend/requirements.txt`. Update `backend/Dockerfile`:

- Replace the hand-maintained pip install list with `RUN pip install .`.
- **CRITICAL: `COPY . .` MUST precede `RUN pip install .`**. pip needs the
  package source (app/ + pyproject.toml) present to install. The current
  Dockerfile has `COPY . .` AFTER the pip step; move it before. This does
  reduce Docker layer cache granularity; accept the tradeoff.
- Confirm `docker build ./backend` still succeeds.

Dependencies:
  - Add `tenacity` to backend/pyproject.toml main deps (not dev/extras).
    Version: ^9 if available, else ^8.
  - Do NOT re-add anything to the deleted requirements.txt.

====================================================================
Tests (backend/tests/services/test_email_service.py)
====================================================================

  a. Happy path: mocked client — send_email returns x-message-id, one INFO log
     with attempt=1.
  b. Sandbox mode: SENDGRID_SANDBOX=True — Mail object passed to mocked client
     has mail_settings.sandbox_mode.enable=True.
  c. No API key: SENDGRID_API_KEY="" — send_email returns "local-<uuid>", mocked
     client is NEVER invoked. Log entry has `event=email_skipped_no_key` and
     contains no `to`, no `subject`, no `body_text`.
  d. Retry on 503: mocked client raises a 503-shaped exception twice, succeeds
     third. Assert 3 attempts + one final success log.
  e. Retry on ConnectionError: same shape with ConnectionError.
  f. No retry on 400: mocked client raises 400-shaped exception. Assert 1
     attempt and the exception propagates.
  g. Missing template ID: SENDGRID_TEMPLATE_VERIFY_EMAIL="" — send_email(
     template=EmailTemplate.VERIFY_EMAIL, ...) raises ValueError before client.
  h. Event loop not blocked: smoke test calls send_email with the mocked client
     configured to sleep() and asserts another coroutine runs concurrently. (If
     to_thread wiring is missing, this test hangs.)
  i. Best-effort callsite preservation: patch send_email to raise after 3
     retries, call POST /auth/register with a valid payload, assert the HTTP
     response is still 201 (registration succeeded, email failed silently).

Run the full auth test suite after updating callsites; every test that
exercised register / verify-resend / forgot / change-email must still pass.

====================================================================
Do NOT
====================================================================

- Ship a Jinja template directory (templates are server-side in SendGrid).
- Wire new routes — only update existing callsites.
- Add any CLI flag for prod; scripts/smoke_email.py is dev/staging only.
- HTML-escape any value passed to dynamic_template_data.
- Use `poetry` anywhere — this project has no poetry. Use `.venv/bin/*`
  for all test / lint / script invocations.

====================================================================
Report
====================================================================

- Every file touched, one-line purpose each.
- Deps added (tenacity version), Settings fields added (should be 7 new + 1
  type change on SENDGRID_API_KEY).
- List of callsites updated (file:line → new signature).
- Full `.venv/bin/pytest` output (pass/fail counts).
- Any callsite found NOT in the 5-row table (audit gap signal).
- Any deviation from this brief.
```

### Phase 1: user — create SendGrid account + empty Dynamic Templates (browser)

1. Log in or create account at https://signup.sendgrid.com/ (free tier = 100/day, plenty for staging).
2. **Sender Authentication** (Settings → Sender Authentication):
   - Authenticate `carddroper.com` via Domain Authentication. SendGrid provides ~3 CNAME records (`s1._domainkey`, `s2._domainkey`, return-path like `em1234`).
   - Add in Cloudflare as CNAMEs, **Proxy status: DNS only** (grey cloud).
   - Click **Verify** in SendGrid. If any fail, wait 15 min for DNS/edge cache.
3. **Create FIVE empty Dynamic Templates** (Email API → Dynamic Templates → Create Template):
   - `carddroper-verify-email`
   - `carddroper-reset-password`
   - `carddroper-change-email`
   - `carddroper-email-changed`          ← NEW canary to OLD address (per readiness audit)
   - `carddroper-credits-purchased`

   Expected template variables per template (wire into the designer, exactly these names):

   | Template | Variables |
   |---|---|
   | `carddroper-verify-email` | `{{verify_url}}`, `{{full_name}}` |
   | `carddroper-reset-password` | `{{reset_url}}`, `{{full_name}}` |
   | `carddroper-change-email` | `{{change_url}}`, `{{full_name}}`, `{{new_email}}` |
   | `carddroper-email-changed` | `{{old_email}}`, `{{new_email}}`, `{{change_date}}`, `{{support_email}}` |
   | `carddroper-credits-purchased` | deferred — leave blank, set up with Stripe receipt ticket |

   Leave HTML empty or use placeholder strings like `"Click here: {{verify_url}}"` for the initial staging smoke. Real copy lands with the consumer tickets.

4. **Create one API key** (Settings → API Keys → Create API Key):
   - Name: `carddroper-staging-mail`
   - Permission: **Restricted Access** → **Mail Send: Full Access**
   - Copy the key once (shown exactly once).

Output for Phase 2: one API key + five template IDs.

### Phase 2: user — upload API key + template IDs as Secret Manager secrets (staging)

Template IDs go into Secret Manager (not env vars) so future ID rotations don't
touch `cloudbuild.yaml`. They aren't secret, but they *are* environment-specific
config and Secret Manager is the single source of truth for that in this project.

```bash
PROJECT=carddroper-staging
SA=carddroper-runtime@$PROJECT.iam.gserviceaccount.com

# 1. API key — sensitive.
echo -n "SG.<KEY>" | gcloud secrets create carddroper-sendgrid-api-key \
    --project=$PROJECT --replication-policy=automatic --data-file=-

# 2-6. Template IDs (5 now, including the new email-changed canary).
for PAIR in \
  "carddroper-sendgrid-template-verify-email:d-<VERIFY_ID>" \
  "carddroper-sendgrid-template-reset-password:d-<RESET_ID>" \
  "carddroper-sendgrid-template-change-email:d-<CHANGE_ID>" \
  "carddroper-sendgrid-template-email-changed:d-<CHANGED_ID>" \
  "carddroper-sendgrid-template-credits-purchased:d-<CREDITS_ID>"; do
    NAME="${PAIR%%:*}"
    VALUE="${PAIR#*:}"
    echo -n "$VALUE" | gcloud secrets create "$NAME" \
        --project=$PROJECT --replication-policy=automatic --data-file=-
done

# 7. Grant runtime SA read access on all 6 secrets.
for NAME in \
  carddroper-sendgrid-api-key \
  carddroper-sendgrid-template-verify-email \
  carddroper-sendgrid-template-reset-password \
  carddroper-sendgrid-template-change-email \
  carddroper-sendgrid-template-email-changed \
  carddroper-sendgrid-template-credits-purchased; do
    gcloud secrets add-iam-policy-binding "$NAME" \
        --project=$PROJECT \
        --member="serviceAccount:$SA" \
        --role=roles/secretmanager.secretAccessor
done
```

Verify:

```bash
gcloud secrets list --project=$PROJECT --filter="name:carddroper-sendgrid*" \
    --format="value(name)" | sort
# Expected (6 rows):
#   projects/.../secrets/carddroper-sendgrid-api-key
#   projects/.../secrets/carddroper-sendgrid-template-change-email
#   projects/.../secrets/carddroper-sendgrid-template-credits-purchased
#   projects/.../secrets/carddroper-sendgrid-template-email-changed
#   projects/.../secrets/carddroper-sendgrid-template-reset-password
#   projects/.../secrets/carddroper-sendgrid-template-verify-email
```

### Phase 3: backend-builder — wire cloudbuild.yaml (agent-executed)

Dispatch **backend-builder**:

```
Task: Extend the backend deploy step in `/Users/johnxing/mini/postapp/cloudbuild.yaml`
  (repo root — NOT backend/cloudbuild.yaml; the file lives at the repo root)
  to mount SendGrid config on the Cloud Run revision.

Edit step 4 (the `gcloud run deploy carddroper-backend` step). The existing
`--set-secrets` line today is exactly:

    --set-secrets=DATABASE_URL=carddroper-database-url:latest,JWT_SECRET=carddroper-jwt-secret:latest

Replace with (single comma-separated argument, no line breaks in the actual YAML):

    --set-secrets=DATABASE_URL=carddroper-database-url:latest,JWT_SECRET=carddroper-jwt-secret:latest,SENDGRID_API_KEY=carddroper-sendgrid-api-key:latest,SENDGRID_TEMPLATE_VERIFY_EMAIL=carddroper-sendgrid-template-verify-email:latest,SENDGRID_TEMPLATE_RESET_PASSWORD=carddroper-sendgrid-template-reset-password:latest,SENDGRID_TEMPLATE_CHANGE_EMAIL=carddroper-sendgrid-template-change-email:latest,SENDGRID_TEMPLATE_EMAIL_CHANGED=carddroper-sendgrid-template-email-changed:latest,SENDGRID_TEMPLATE_CREDITS_PURCHASED=carddroper-sendgrid-template-credits-purchased:latest

Add a NEW `--set-env-vars` argument to the same deploy step with:

    --set-env-vars=SENDGRID_SANDBOX=false,FROM_EMAIL=noreply@carddroper.com,FROM_NAME=Carddroper,FRONTEND_BASE_URL=https://staging.carddroper.com

Do NOT merge. Report the diff; orchestrator merges to main after review.
```

Orchestrator reviews and merges to `main` to trigger the deploy.

### Phase 4: user — staging smoke test (real email send, no key in shell history)

> **PAUSED 2026-04-21.** Ticket 0013 (testing methodology) is establishing
> the staging-smoke pattern so `smoke_email.py` lands as one of a suite
> rather than a one-off. Resume this phase at 0013's Phase 5. Phases 0–3
> of 0010 remain complete and committed (c4fb874, 4d846ba).

The backend build from Phase 3 must be `SUCCESS` first. Then run the local
smoke against the real staging key, pulled from Secret Manager into a subshell.

**This project uses a plain `.venv` — NOT poetry.** All invocations use
`.venv/bin/python` directly.

```bash
cd /Users/johnxing/mini/postapp/backend

# 1. Dry run — no key needed, confirms smoke script imports cleanly.
SENDGRID_API_KEY= .venv/bin/python scripts/smoke_email.py \
    --to="<your-personal-email>" --template=VERIFY_EMAIL
# Expected: logs event=email_skipped_no_key with dev_preview_url; returns "local-<uuid>".

# 2. Real send — key pulled inline from Secret Manager, scoped to this one command.
SENDGRID_API_KEY="$(gcloud secrets versions access latest \
    --secret=carddroper-sendgrid-api-key \
    --project=carddroper-staging)" \
SENDGRID_TEMPLATE_VERIFY_EMAIL="$(gcloud secrets versions access latest \
    --secret=carddroper-sendgrid-template-verify-email \
    --project=carddroper-staging)" \
FROM_EMAIL=noreply@carddroper.com \
FROM_NAME=Carddroper \
FRONTEND_BASE_URL=https://staging.carddroper.com \
  .venv/bin/python scripts/smoke_email.py \
    --to="<your-personal-email>" --template=VERIFY_EMAIL
# Expected: prints "sg_message_id=..." and exits 0.
```

The key lives only in the subshell's environment for the single command —
`~/.zsh_history` sees `$(gcloud ...)` as text, not the key.

Expected in your inbox within 30 seconds:

- Email with your chosen template's placeholder contents rendered.
- `From: Carddroper <noreply@carddroper.com>`.
- Gmail → three dots → **Show original** shows `DKIM: 'PASS' with domain carddroper.com`.
- SendGrid Activity Feed shows the send event with status `Delivered`.

If DKIM shows `FAIL` or `NONE`, wait 15 minutes (Cloudflare → Google edge DKIM
caching lag) and send again before escalating.

**Note:** clicking the `{{verify_url}}` link in the delivered email will 404
until ticket 0011 ships the `/verify-email` Next.js page. The smoke goal here is
DKIM + delivery confirmation, not end-to-end verify.

## Verification

**Automated checks (backend-builder, reported inside Phase 0):**

```bash
cd backend
.venv/bin/pytest tests/services/test_email_service.py -v   # new tests
.venv/bin/pytest                                           # full suite, 0 regressions
.venv/bin/ruff check app/ tests/ scripts/
```

**Functional smoke (user, staging, after Phase 3 deploy):**

- `gcloud builds list --region=us-west1 --limit=1 --format="value(status)"` → `SUCCESS`.
- `gcloud run services describe carddroper-backend --region=us-west1 --format="value(spec.template.spec.containers[0].env)"` shows `SENDGRID_SANDBOX=false`, `FROM_EMAIL=...`, `FROM_NAME=...`, `FRONTEND_BASE_URL=https://staging.carddroper.com`, and the six secret-backed env vars.
- Real email arrives with `From: Carddroper <noreply@carddroper.com>`.
- Gmail "Show original": DKIM=PASS, domain=carddroper.com.
- SendGrid Activity Feed shows the send within 1 minute.
- Cloud Run logs for the send include a structured line:
  `{"event":"email_sent","template":"VERIFY_EMAIL","to_hash":"<sha256 hex>","sg_message_id":"<x-message-id>","attempt":1}`.

## Out of scope

- Wiring `send_email` into *new* routes (email verification polish = ticket 0011; Stripe receipt = its own ticket). This ticket only updates *existing* callsites from the scaffold.
- **Frontend routes consuming the verify / reset / change URLs.** Ticket 0011 creates `/verify-email`, `/verify-email-sent`, `/reset-password`, `/confirm-email-change`. Until 0011 lands, the email links in 0010's smoke will resolve to Next.js 404s. Acceptable: staging has no real users and Phase 4 smoke is manual (personal inbox). v0.1.0 launch blocks on 0011.
- SPF / DMARC tuning beyond the DKIM CNAMEs SendGrid provides. Pre-launch operational item.
- MX / inbound mail for `support@`, `privacy@`, `legal@carddroper.com`. Pre-launch operational item.
- Prod SendGrid account + secrets. Lands with the prod stand-up ticket.
- Multi-tenant senders. `from_address` / `from_name` are parameters so v0.2 can use them; v0.1 always passes defaults.
- Email open/click tracking and bounce/complaint webhook handling. Deferred to v0.2.
- Background-task / queue-based email dispatch. v0.1 sends inline with tenacity + 5s per-attempt timeout (~36s worst-case). Move to queue in v0.2 if latency becomes a UX issue.

## Report

Backend-builder (Phases 0 and 3):
- Files touched + one-line purpose.
- Deps added (name + version).
- Settings fields added.
- Callsites updated (file:line → new call shape).
- Full `.venv/bin/pytest` output.
- Any callsites found NOT listed in the 5-row table (audit gap).
- Any deviations.

User (Phases 1, 2, 4):
- SendGrid domain auth: three CNAMEs, all green.
- Five template IDs + API key uploaded to Secret Manager (paste the 6 secret names, not values).
- Inbox screenshot or timestamp of smoke email.
- Gmail "Show original" DKIM=PASS line.

## Resolution

Resolved 2026-04-21. SendGrid infrastructure landed across four phases; Phase 4 staging smoke ran under ticket 0013 Phase 5 using the now-codified `scripts/smoke_email.py` pattern.

**Phases delivered:**

- **Phase 0 (backend, commit c4fb874).** `app/services/email_service.py` reshaped to the production spec: `EmailTemplate` enum (5 members including `EMAIL_CHANGED`), async `send_email(template, to, dynamic_template_data)` with singleton SendGrid client, `asyncio.to_thread` offload, 5s per-attempt timeout, tenacity retry (3 attempts, 1s→4s→16s exp backoff on `Timeout`/`ConnectionError`/{429,500,502,503,504}), sandbox mode, `SecretStr` API key, SHA-256 `to_hash` structured logging, no-key fallback with `dev_preview_url`. All 5 `routes/auth.py` callsites migrated to the new signature with best-effort `try/except` semantics. Settings extended with `SENDGRID_API_KEY: SecretStr`, `SENDGRID_SANDBOX`, 5 template IDs, `FROM_EMAIL`, `FROM_NAME`, `FRONTEND_BASE_URL`. 9 service-level tests + 1 callsite test added.
- **Phases 1–2 (user).** SendGrid domain authentication completed (3 CNAMEs green); 5 template IDs + API key uploaded to Secret Manager as 6 secrets; runtime SA granted `secretmanager.secretAccessor` on each via the IAM binding loop.
- **Phase 3 (orchestrator, commit 4d846ba).** `cloudbuild.yaml` Step 4 extended — `--set-secrets` now mounts all 6 SendGrid secrets; `--set-env-vars` adds `SENDGRID_SANDBOX=false`, `FROM_EMAIL=noreply@carddroper.com`, `FROM_NAME=Carddroper`, `FRONTEND_BASE_URL=https://staging.carddroper.com`.
- **Phase 4 (user, under 0013 Phase 5, commit d2750dd).** `scripts/smoke_email.py` (rewritten to the `testing.md` pattern with pre-flight guards refusing fallback success) executed against `https://api.staging.carddroper.com`. Real `sg_message_id=VLDjJRHJQ82yKuCFngsAlg` returned on first attempt (`attempt: 1`), `email_sent` structured log emitted, `SMOKE OK: email` marker printed. Proves: live API key valid, template ID correctly mounted from Secret Manager, `dynamic_template_data` contract matches the template, async offload + retry wrapper work under real latency.

**Deviations from the original brief:**

1. `email_service.py` lives at `app/services/email_service.py`, not `app/email_service.py`. Phase 0 agent moved it into `app/services/` without flagging. Non-breaking — all imports updated consistently. Kept as-is since it matches the `app/services/*` convention already established by `auth_service.py`.
2. The register endpoint returns HTTP 200, not the 201 the ticket text called out. The existing suite always asserted 200; the new `test_register_succeeds_when_email_send_raises` test follows suit with an inline comment documenting the deviation. Endpoint contract unchanged from what was already in production; no caller affected.
3. Post-Phase-0 packaging bug surfaced on Cloud Build: Phase 0's Dockerfile swap from hand-listed `pip install` to `pip install .` triggered setuptools ≥61 auto-discovery to find both `app/` and `alembic/` as flat-layout top-level packages, producing "Multiple top-level packages discovered" and failing the build. Fix: added `[tool.setuptools.packages.find]` with `include = ["app*"]` to `backend/pyproject.toml`. `alembic/` continues to be `COPY`d into the image for the migration step but is not installed as a Python package. This class of bug was not caught by `pytest` (source-in-place) and motivated adding a `docker build` checklist item to `doc/operations/testing.md` §Per-ticket checklist.

**Acceptance trace.** All 10 items (A1–A10) from the Acceptance section satisfied. §Verification automated checks: `pytest` green (36 passing at ticket close), `ruff check .` + `ruff format --check .` clean. §Verification functional smoke: `smoke_email.py` returned `SMOKE OK: email` with a real SendGrid message ID against staging.

**Commits:** c4fb874 (Phase 0 backend), 4d846ba (Phase 3 cloudbuild.yaml), d2750dd (Phase 5 staging smoke under 0013).
