---
id: 0010
title: SendGrid infrastructure — hardened send_email() helper + staging secret wiring
status: open
priority: high
found_by: orchestrator 2026-04-20
---

## Context

PLAN.md §10.5 (email layer) and §10.6 (Stripe) both consume this service. This ticket lands a production-grade email helper *before* either layer calls it, so we never retrofit retries / templating / async-offload across live callsites.

The scaffold left behind `backend/app/services/email_service.py` (and it is **already consumed** by `backend/app/routes/auth.py` — register, verify-resend, forgot-password, change-email). Ground-truth callsites are in ticket 0009's audit report — this ticket reshapes the helper *and* updates every existing callsite to the new signature.

**Improvements over the foodapp pattern** (all to be delivered):

1. **Singleton SendGrid client.** Instantiate once at FastAPI startup (`main.py` lifespan), reuse across requests. foodapp instantiated per-call.
2. **Async offload for the sync SDK.** `sendgrid.SendGridAPIClient` is a blocking `requests`-based client. All calls out of `send_email()` must go through `asyncio.to_thread(...)` (or an equivalent thread-pool offload). Without this, every send blocks the FastAPI event loop — a latent concurrency bug that is invisible under low load.
3. **Per-attempt HTTP timeout of 5 seconds.** Configure on the SendGrid client's underlying session (or via `httpx.AsyncClient` if the builder chooses to rewrite). Prevents a hung TLS handshake from blocking for Cloud Run's full 5-minute request budget.
4. **Tenacity retry with exponential backoff.** 3 attempts, 1s → 4s → 16s. Retry predicate: network errors (`requests.Timeout`, `requests.ConnectionError`) **or** any caught exception where `getattr(exc, "status_code", None) in {429, 500, 502, 503, 504}`. Do not retry on other 4xx — those are developer errors, failing loud is correct.
5. **Dynamic Templates.** Every outbound email uses a server-side template ID. Code passes `dynamic_template_data: dict`. No hardcoded HTML in the codebase.
6. **Typed `EmailTemplate` enum.** Members: `VERIFY_EMAIL`, `RESET_PASSWORD`, `CHANGE_EMAIL`, `CREDITS_PURCHASED`. Each resolves to a Settings field carrying the SendGrid template ID. `send_email(template: EmailTemplate, ...)` — no loose strings.
7. **Structured logging.** Each send emits `{event: "email_sent", template, to_hash, sg_message_id, attempt}` where `to_hash` is SHA-256 of the lowercased email. PII-safe tracing. On failure, log the full exception + `status_code` at `ERROR`.
8. **Sandbox mode.** When `SENDGRID_SANDBOX=true`, set `mail_settings.sandbox_mode.enable=True` on the Mail object. SendGrid validates the payload but never delivers. Default `false` in staging/prod; `true` in tests.
9. **`from_address` / `from_name` as parameters** (defaults from `FROM_EMAIL` / `FROM_NAME`). Forward-compat for v0.2 multi-tenant.
10. **Settings repr hides the key.** Pydantic `SecretStr` for `SENDGRID_API_KEY` so logs / `/health?debug=true` never leak the raw key.

## Pre-requisites

- Ticket 0008 resolved (staging custom domains live).
- Ticket 0009 (code audit) resolved (2026-04-20). Relevant extracts from `doc/audits/2026-04-20-backend-audit.md` are inlined below — this ticket does not require re-reading that file.
- Access to the Carddroper SendGrid account (user must create if new).

**Current `email_service.py` public API** (to be fully reshaped in Phase 0):

```python
def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool
def send_verification_email(email: str, token: str, full_name: Optional[str] = None) -> bool
def send_password_reset(email: str, token: str, full_name: Optional[str] = None) -> bool
def send_email_change_verification(new_email: str, token: str, full_name: Optional[str] = None) -> bool
def send_email_change_notification(old_email: str, new_email: str) -> bool
```

**All callsites** (all in `backend/app/routes/auth.py`, all currently wrap the send in `asyncio.to_thread(...)` at the outer helper level — Phase 0 moves the offload *inside* `send_email` so callsites just `await send_email(...)`):

| Line | Endpoint | Current call |
|---|---|---|
| L241 | `register` | `send_verification_email(user.email, verify_token, user.full_name)` |
| L399 | `forgot_password` | `send_password_reset(user.email, token, user.full_name)` |
| L494 | `resend_verification` | `send_verification_email(current_user.email, token, current_user.full_name)` |
| L525-L527 | `change_email` | `send_email_change_verification(body.new_email, token, current_user.full_name)` |
| L569 | `confirm_email_change` | `send_email_change_notification(old_email, new_email)` |

**Audit-derived fixes folded into Phase 0:**

- **F-2 (medium, security):** the current "no API key" dev fallback at `email_service.py:20` logs `{"to": to, "body_text": text or html[:500]}` — the `body_text` contains the full verification/reset link (i.e. a valid auth token) in production-shape logs. Phase 0 must drop `body_text` entirely from the no-key fallback log and replace `to` with the same `to_hash` used on the real-send path. Safe log fields in the no-key branch: `template`, `to_hash`, `mock_message_id`. Nothing else.
- **F-4 (medium, dep-hygiene):** `backend/requirements.txt` duplicates `pyproject.toml` and the `Dockerfile` installs via a third hand-maintained `pip install` list. Phase 0 deletes `backend/requirements.txt` and updates the `Dockerfile` to install via `pip install .` against `pyproject.toml`. tenacity gets added to `pyproject.toml` (not anywhere else).

**Confirm staging still green:**

Confirm staging still green:

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
  (L241, L399, L494, L525-L527, L569). Every callsite in that table must switch
  to the new send_email signature. The four helper functions (send_verification_email,
  send_password_reset, send_email_change_verification, send_email_change_notification)
  are dropped — their callers move to the new typed send_email directly.

  If you find an email_service callsite outside that table, report it and still
  update it.

Deliverables:

  1. Public API:
       class EmailTemplate(str, Enum):
           VERIFY_EMAIL = "VERIFY_EMAIL"
           RESET_PASSWORD = "RESET_PASSWORD"
           CHANGE_EMAIL = "CHANGE_EMAIL"
           CREDITS_PURCHASED = "CREDITS_PURCHASED"

       async def send_email(
           *,
           template: EmailTemplate,
           to: str,
           dynamic_template_data: dict,
           from_address: str | None = None,
           from_name: str | None = None,
       ) -> str  # returns the SendGrid x-message-id

  2. Singleton SendGrid client built once at module import. Expose
     an init_email_client() / close_email_client() pair wired into
     main.py's FastAPI lifespan, even if close is a no-op today.

  3. ALL outbound calls go through asyncio.to_thread(client.send, mail)
     (the SDK is sync; calling it directly on the event loop is a bug).
     Per-attempt HTTP timeout = 5.0s, set on the client's requests session.

  4. Tenacity retry:
       - 3 attempts, exponential wait 1s → 4s → 16s.
       - Retry on: requests.Timeout, requests.ConnectionError, OR any exception
         where getattr(exc, "status_code", None) in {429, 500, 502, 503, 504}.
       - Do NOT retry on other 4xx.
       - Each attempt emits a structured log line with `attempt` incremented.

  5. If Settings.SENDGRID_API_KEY is empty: send_email logs ONLY
       {"event":"email_skipped_no_key", "template":<name>, "to_hash":<sha256 hex>,
        "mock_message_id":"local-<uuid4>"}
     and returns "local-<uuid4>". Makes no network call. Local dev works without a key.
     (Audit F-2: the current implementation logs body_text which contains the full
     token URL — that MUST go. Do not log dynamic_template_data contents, do not log
     the raw "to" address, do not log the subject.)

  6. Sandbox: Settings.SENDGRID_SANDBOX (bool, default False). When True, set
     mail.mail_settings.sandbox_mode.enable = True. Network call still happens;
     SendGrid returns 200 without delivering.

  7. Settings additions (all in backend/app/config.py):
       SENDGRID_API_KEY: SecretStr = SecretStr("")   # pydantic SecretStr so repr hides it
       SENDGRID_SANDBOX: bool = False
       SENDGRID_TEMPLATE_VERIFY_EMAIL: str = ""
       SENDGRID_TEMPLATE_RESET_PASSWORD: str = ""
       SENDGRID_TEMPLATE_CHANGE_EMAIL: str = ""
       SENDGRID_TEMPLATE_CREDITS_PURCHASED: str = ""
       FROM_EMAIL: str = "noreply@carddroper.com"
       FROM_NAME: str = "Carddroper"

     EmailTemplate → settings-field resolution via a module-level dict.
     If the resolved template ID is empty at send time, raise ValueError
     ("SENDGRID_TEMPLATE_<NAME> is not configured"). Fail loud on misconfig.

  8. Structured logging: use the existing app logger. Log line shape:
       INFO:  {"event":"email_sent","template":"VERIFY_EMAIL","to_hash":"<hex>",
               "sg_message_id":"<x-message-id>","attempt":1}
       ERROR: {"event":"email_send_failed","template":"...","to_hash":"...",
               "status_code":<int or null>,"attempt":<int>,"error":"<exc class>"}

  9. Update backend/.env.example to include all new fields with safe defaults
     (empty key, sandbox=false for parity with prod, empty template IDs).

  10. Add backend/scripts/smoke_email.py — a tiny CLI that takes --to=<addr> and
      --template=<name> and calls send_email. Used by Phase 4 for the live
      staging send. Reads config from env (same Settings as the app).

  11. Update EVERY callsite of email_service in routes/auth.py (the 5 listed in
      Pre-requisites: L241, L399, L494, L525-L527, L569) to the new send_email
      signature. The four helper wrappers (send_verification_email,
      send_password_reset, send_email_change_verification,
      send_email_change_notification) are deleted — no meaningful wrapping survives
      the reshape because templates are now server-side and HTML construction moves
      out of Python entirely. Each former helper becomes a direct send_email call
      at the route, passing the appropriate EmailTemplate enum + dynamic_template_data
      dict.

  12. Audit F-4 (dep hygiene): delete backend/requirements.txt. Update
      backend/Dockerfile to install runtime deps via `pip install .` against
      pyproject.toml (currently a hand-maintained pip install list that duplicates
      pyproject.toml). Confirm the build still succeeds with
      `docker compose build backend` (or at minimum `docker build ./backend`).

Dependencies:
  - Add tenacity to backend/pyproject.toml main deps (not dev/extras).
    Version: ^9 if available, else ^8.
  - DO NOT add tenacity or any other dep to the deleted requirements.txt path.

Tests (backend/tests/services/test_email_service.py):
  a. Happy path: mocked client — send_email returns "X-Message-Id" header value,
     emits one structured INFO log with attempt=1.
  b. Sandbox mode: SENDGRID_SANDBOX=True — the Mail object passed to the mocked
     client has mail_settings.sandbox_mode.enable=True.
  c. No API key: SENDGRID_API_KEY="" — send_email returns "local-<uuid>", mocked
     client is never invoked.
  d. Retry on 503: mocked client raises a 503-shaped exception twice, then succeeds.
     Assert 3 attempts + one final success log.
  e. Retry on ConnectionError: same shape, but raise ConnectionError.
  f. No retry on 400: mocked client raises a 400-shaped exception. Assert 1 attempt
     and the exception propagates.
  g. Missing template ID: SENDGRID_TEMPLATE_VERIFY_EMAIL="" — send_email(template=
     EmailTemplate.VERIFY_EMAIL, ...) raises ValueError before the client is called.
  h. Event loop not blocked: can be a smoke test that calls send_email with the
     mocked client configured to sleep() and asserts another coroutine runs
     concurrently. (If the to_thread wiring is missing, this test hangs.)

Also run the existing auth test suite after updating callsites. Every existing test
  that covered register / verify-resend / forgot / change-email must still pass,
  potentially with updated mock targets if the function being patched moved.

Do NOT:
  - Ship a Jinja template directory (templates are server-side in SendGrid).
  - Wire new routes — only update existing callsites.
  - Add any CLI flag for prod; scripts/smoke_email.py is dev/staging only.

Report:
  - Every file touched, one-line purpose each.
  - Deps added (tenacity version), Settings fields added.
  - List of callsites updated (file:line → new signature).
  - Full pytest output (pass/fail counts).
  - Any callsite found NOT in the 0009 audit (so orchestrator can flag the audit gap).
  - Any deviation from this brief.
```

### Phase 1: user — create SendGrid account + empty Dynamic Templates (browser)

1. Log in or create account at https://signup.sendgrid.com/ (free tier = 100/day, plenty for staging).
2. **Sender Authentication** (Settings → Sender Authentication):
   - Authenticate `carddroper.com` via Domain Authentication. SendGrid gives you ~3 CNAME records (`s1._domainkey`, `s2._domainkey`, return-path like `em1234`).
   - Add those in Cloudflare as CNAMEs, **Proxy status: DNS only** (grey cloud).
   - Click **Verify** in SendGrid. Re-check if any fail — DKIM propagation on Cloudflare is usually under a minute, but edge caching occasionally needs 5-15 min. If "Show original" in Gmail later shows DKIM=fail, wait 15 min before escalating.
3. **Create four empty Dynamic Templates** (Email API → Dynamic Templates → Create Template). Name them:
   - `carddroper-verify-email`
   - `carddroper-reset-password`
   - `carddroper-change-email`
   - `carddroper-credits-purchased`

   **Do not fill in HTML/variables yet.** The consumer tickets (0011 email verify, 0013 Stripe receipts, etc.) each lock their own variable names and design the final copy. For now just create the templates, save the IDs (`d-abc123...`).
4. **Create one API key** (Settings → API Keys → Create API Key):
   - Name: `carddroper-staging-mail`
   - Permission: **Restricted Access** → only **Mail Send: Full Access**
   - Copy the key once (it is shown exactly once).

Output for Phase 2: one API key + four template IDs.

### Phase 2: user — upload API key + template IDs as Secret Manager secrets (staging)

Template IDs go into Secret Manager (not env vars) so future ID rotations don't touch `cloudbuild.yaml`. They aren't secret, but they *are* environment-specific config and Secret Manager is the single source of truth for that in this project.

```bash
PROJECT=carddroper-staging
SA=carddroper-runtime@$PROJECT.iam.gserviceaccount.com

# 1. API key — sensitive.
echo -n "SG.<KEY>" | gcloud secrets create carddroper-sendgrid-api-key \
    --project=$PROJECT --replication-policy=automatic --data-file=-

# 2-5. Template IDs — not sensitive, but same storage for uniform plumbing.
for PAIR in \
  "carddroper-sendgrid-template-verify-email:d-<VERIFY_ID>" \
  "carddroper-sendgrid-template-reset-password:d-<RESET_ID>" \
  "carddroper-sendgrid-template-change-email:d-<CHANGE_ID>" \
  "carddroper-sendgrid-template-credits-purchased:d-<CREDITS_ID>"; do
    NAME="${PAIR%%:*}"
    VALUE="${PAIR#*:}"
    echo -n "$VALUE" | gcloud secrets create "$NAME" \
        --project=$PROJECT --replication-policy=automatic --data-file=-
done

# 6. Grant runtime SA read access on all five.
for NAME in \
  carddroper-sendgrid-api-key \
  carddroper-sendgrid-template-verify-email \
  carddroper-sendgrid-template-reset-password \
  carddroper-sendgrid-template-change-email \
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
# Expected (5 rows):
#   projects/.../secrets/carddroper-sendgrid-api-key
#   projects/.../secrets/carddroper-sendgrid-template-change-email
#   projects/.../secrets/carddroper-sendgrid-template-credits-purchased
#   projects/.../secrets/carddroper-sendgrid-template-reset-password
#   projects/.../secrets/carddroper-sendgrid-template-verify-email
```

### Phase 3: backend-builder — wire cloudbuild.yaml (agent-executed)

Dispatch **backend-builder**:

```
Task: Extend cloudbuild.yaml backend deploy step (step 4) to mount SendGrid config.

Edit the --set-secrets line on the backend Cloud Run deploy so all five SendGrid
secrets are mounted as env vars. Keep existing DATABASE_URL and JWT_SECRET entries.

Resulting --set-secrets value (single comma-separated argument):
  DATABASE_URL=carddroper-database-url:latest,
  JWT_SECRET=carddroper-jwt-secret:latest,
  SENDGRID_API_KEY=carddroper-sendgrid-api-key:latest,
  SENDGRID_TEMPLATE_VERIFY_EMAIL=carddroper-sendgrid-template-verify-email:latest,
  SENDGRID_TEMPLATE_RESET_PASSWORD=carddroper-sendgrid-template-reset-password:latest,
  SENDGRID_TEMPLATE_CHANGE_EMAIL=carddroper-sendgrid-template-change-email:latest,
  SENDGRID_TEMPLATE_CREDITS_PURCHASED=carddroper-sendgrid-template-credits-purchased:latest

Add --set-env-vars (new argument) on the same deploy step with:
  SENDGRID_SANDBOX=false,FROM_EMAIL=noreply@carddroper.com,FROM_NAME=Carddroper

Do NOT merge. Report the diff; orchestrator merges to main after review.
```

Orchestrator reviews and merges to `main` to trigger the deploy.

### Phase 4: user — staging smoke test (real email send, no key in shell history)

The backend build from Phase 3 must be `SUCCESS` first. Then run the local REPL against the real staging key, pulled from Secret Manager into a subshell — never into your shell history.

```bash
cd /Users/johnxing/mini/postapp/backend

# 1. Dry run — no key needed, confirms the smoke script imports cleanly.
SENDGRID_API_KEY= poetry run python scripts/smoke_email.py \
    --to="<your-personal-email>" --template=VERIFY_EMAIL
# Expected: logs a payload, returns "local-<uuid>".

# 2. Real send — key pulled inline from Secret Manager, scoped to this one command.
SENDGRID_API_KEY="$(gcloud secrets versions access latest \
    --secret=carddroper-sendgrid-api-key \
    --project=carddroper-staging)" \
SENDGRID_TEMPLATE_VERIFY_EMAIL="$(gcloud secrets versions access latest \
    --secret=carddroper-sendgrid-template-verify-email \
    --project=carddroper-staging)" \
FROM_EMAIL=noreply@carddroper.com \
FROM_NAME=Carddroper \
  poetry run python scripts/smoke_email.py \
    --to="<your-personal-email>" --template=VERIFY_EMAIL
# Expected: prints "sg_message_id=..." and exits 0.
```

The key lives only in the subshell's environment for the single command — `~/.zsh_history` sees `$(gcloud ...)` as text, not the key.

Expected in your inbox within 30 seconds:
- Email with your chosen template's placeholder contents.
- `From: Carddroper <noreply@carddroper.com>`.
- Gmail → three dots → **Show original** shows `DKIM: 'PASS' with domain carddroper.com`.
- SendGrid Activity Feed shows the send event with status `Delivered`.

If DKIM shows `FAIL` or `NONE`, wait 15 minutes (Cloudflare → Google edge DKIM caching lag) and send again before escalating. The failure mode usually means sent-too-early after DNS update, not a real config bug.

## Verification

**Automated checks (backend-builder, reported inside Phase 0):**

```bash
cd backend
poetry run pytest tests/services/test_email_service.py -v   # new tests
poetry run pytest                                            # full suite, 0 regressions
poetry run ruff check app/ tests/ scripts/
```

**Functional smoke (user, staging, after Phase 3 deploy):**

- `gcloud builds list --region=us-west1 --limit=1 --format="value(status)"` → `SUCCESS`.
- `gcloud run services describe carddroper-backend --region=us-west1 --format="value(spec.template.spec.containers[0].env)"` shows `SENDGRID_SANDBOX=false`, `FROM_EMAIL=...`, `FROM_NAME=...`, and the secret-backed env vars.
- Real email arrives with `From: Carddroper <noreply@carddroper.com>`.
- Gmail "Show original": DKIM=PASS, domain=carddroper.com.
- SendGrid Activity Feed shows the send within 1 minute.
- Cloud Run logs for the send include a structured line: `{"event":"email_sent","template":"VERIFY_EMAIL","to_hash":"<sha256 hex>","sg_message_id":"<x-message-id>","attempt":1}`.

## Out of scope

- Wiring `send_email` into *new* routes (email verification polish = ticket 0011; Stripe receipt = ticket with the Stripe webhook). This ticket only updates *existing* callsites from the scaffold.
- SPF / DMARC tuning beyond the DKIM CNAMEs SendGrid provides. Pre-launch operational item.
- MX / inbound mail for `support@`, `privacy@`, `legal@`. Pre-launch operational item.
- Prod SendGrid account + secrets. Lands with the prod stand-up ticket.
- Multi-tenant senders. `from_address` / `from_name` are parameters so v0.2 can use them; v0.1 always passes defaults.
- Email open/click tracking and bounce/complaint webhook handling. Deferred to v0.2. We rely on SendGrid's built-in suppression list to protect sender reputation in the meantime.
- Background-task / queue-based email dispatch. v0.1 sends inline on the request with tenacity + 5s per-attempt timeout (~36s worst-case). Move to async queue in v0.2 if latency becomes a UX issue.

## Report

Backend-builder (Phases 0 and 3):
- Files touched + one-line purpose.
- Deps added (name + version).
- Settings fields added.
- Callsites updated (file:line → new call shape).
- Full pytest output.
- Any callsites found NOT listed in 0009 audit (indicates audit gap).
- Any deviations.

User (Phases 1, 2, 4):
- SendGrid domain auth: three CNAMEs, all green.
- Four template IDs + API key uploaded to Secret Manager (paste the 5 secret names, not values).
- Inbox screenshot or timestamp of smoke email.
- Gmail "Show original" DKIM=PASS line.

## Resolution

*(filled in by orchestrator on close)*
