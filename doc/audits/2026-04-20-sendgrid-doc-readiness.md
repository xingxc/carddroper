# SendGrid Readiness — Doc + Architecture Review — 2026-04-20

**Scope:** Cross-check ticket 0010's concrete claims against `doc/PLAN.md`,
`doc/systems/auth.md`, `doc/architecture/*.md`, `doc/operations/*.md`,
`cloudbuild.yaml`, and the 2026-04-20 backend + frontend audit reports.
Orchestrator-layer review. The code layer runs in parallel via
`backend-builder` + `frontend-builder`.

**Purpose:** Surface drift between 0010's plan and the documented system before
dispatching implementation, so Phase 0 can start from a ticket that's
self-consistent with the rest of the project.

---

## Findings

### D-1 — `cloudbuild.yaml` is at the repo root, not `backend/` (severity: medium)

**Claim in 0010 Phase 3:** *"Extend cloudbuild.yaml backend deploy step (step 4)…"*
The implementing-agent brief never names the absolute path, but Phase 3
reads as if the file lives under `backend/`. Actual location:
`/cloudbuild.yaml` at the repo root.

**Fix before dispatch:** Phase 3 brief should say "Edit `cloudbuild.yaml` at
the repo root, step 4 (backend Cloud Run deploy)." One line.

---

### D-2 — `EmailTemplate` enum is missing the old-address canary template (severity: HIGH — scope-breaking)

**Claim in 0010 Phase 0:** 4-member enum:
```
VERIFY_EMAIL, RESET_PASSWORD, CHANGE_EMAIL, CREDITS_PURCHASED
```
**Claim in 0010 Phase 1:** 4 Dynamic Templates in SendGrid.

**Documented reality (`doc/systems/auth.md` §Email change, step 5):** the email
change flow sends a **canary notification to the OLD address** after
confirmation — distinct content from the verification link, no clickable token,
just *"Your email on carddroper was changed to `<new_email>` on `<date>`. If
this wasn't you, contact `support@carddroper.com` immediately."* This is the
core defense against silent account takeover.

**Scaffold reality** (per `doc/audits/2026-04-20-backend-audit.md` §4): fifth
sender `send_email_change_notification(old_email, new_email)` at
`routes/auth.py:L569` covers exactly this canary.

**Drift:** 0010's enum has no member, no SendGrid template, no Settings field,
and no Secret Manager entry for this canary. The L569 callsite in the 5-row
table has nowhere to land under the new API as written. An implementing agent
will either (a) invent a 5th member (deviation), (b) re-use `CHANGE_EMAIL` with
a boolean flag inside `dynamic_template_data` and branch inside the SendGrid
template (fragile), or (c) drop the canary entirely (contradicts auth.md and
weakens a defense-in-depth promise).

**Fix before dispatch:** add everywhere — 5th member
`EMAIL_CHANGED`, 5th Dynamic Template `carddroper-email-changed`,
5th Settings field `SENDGRID_TEMPLATE_EMAIL_CHANGED`, 5th Secret Manager
entry `carddroper-sendgrid-template-email-changed`, 5th env in `--set-secrets`.
Phase 1 now creates 5 templates, not 4.

---

### D-3 — "Email send is best-effort" semantic not preserved in 0010 (severity: HIGH)

**Current behavior** (per `backend-audit.md` §4 callouts, line 158): *"All are
wrapped in `try/except Exception` with `logger.error`. Email failures are
best-effort; they do not abort the request or roll back the DB write."*

**0010 Phase 0 brief silence:** the brief says the new `send_email` "returns the
SendGrid x-message-id" and emits an ERROR log on final failure. It does not say
what happens at the callsite if `send_email` raises after 3 tenacity retries.

**Drift risk:** an implementing agent following the brief verbatim may write
`message_id = await send_email(...)` without a try/except, because the
"offload is moving inside." A final tenacity failure would then propagate
out of the handler and be caught by the new global 500 handler (ticket 0011).
The `register` endpoint would return 500 to a user whose account was already
persisted to the DB — a broken UX and a divergence from the existing contract.

**Fix before dispatch:** Phase 0 brief must explicitly state:

> Each of the 5 callsites MUST wrap `await send_email(...)` in its own
> `try/except Exception` block. On exception, log with the existing
> `logger.exception(...)` pattern and continue — the enclosing HTTP handler
> must NOT fail because email delivery failed. Email is best-effort. The
> response body stays unchanged.

Add a test (existing auth flow suite already exercises register + forgot +
change-email; simulate `send_email` raising and assert the HTTP status is still
200/201).

---

### D-4 — No `dynamic_template_data` key contract in 0010 (severity: HIGH)

**Drift:** 0010 deletes the helper wrappers and the four current callsites move
to direct `send_email(template=..., dynamic_template_data={...})` calls. The
brief never specifies what keys go in each template's data. SendGrid Dynamic
Templates reference variables by name; backend payload and template design must
agree. Without an explicit contract:

- Phase 1 user doesn't know what variables to declare in SendGrid templates.
- Phase 0 agent guesses, then reality disagrees at Phase 4 smoke.

**Fix before dispatch:** inline this table into 0010 Phase 0 (and cross-ref
from Phase 1):

| Template | `dynamic_template_data` keys |
|---|---|
| `VERIFY_EMAIL` | `verify_url` (absolute URL w/ token), `full_name` (nullable) |
| `RESET_PASSWORD` | `reset_url`, `full_name` |
| `CHANGE_EMAIL` | `change_url`, `full_name`, `new_email` |
| `EMAIL_CHANGED` (per D-2) | `old_email`, `new_email`, `change_date` (ISO-8601 UTC), `support_email` |
| `CREDITS_PURCHASED` | deferred to Stripe receipt ticket — document as `TBD` |

`full_name` is `Optional[str]` on `User`; templates must handle the null case
gracefully (fallback copy like "Hi there," when absent).

---

### D-5 — No `FRONTEND_BASE_URL` Settings field; URL construction undefined (severity: HIGH)

**Drift:** three of the four templates include an absolute URL (the verify /
reset / change link). That URL must be built somewhere with `{base}/verify-email?token=<jwt>`.

Options:
- **(a)** Backend builds full URL from a new `Settings.FRONTEND_BASE_URL`, passes
  as `verify_url` in `dynamic_template_data`.
- **(b)** SendGrid template hard-codes the base, backend passes only `token`.
  This forces separate templates per environment — contradicts the single-ID
  approach 0010 uses.

**(a) is correct.** 0010 does not name `FRONTEND_BASE_URL` as a Settings field,
does not add it to `.env.example`, and does not wire it into `cloudbuild.yaml`
`--set-env-vars`.

**Fix before dispatch:** add to Phase 0 Settings list:
```
FRONTEND_BASE_URL: str = "http://localhost:3000"
```
Add to `.env.example`. In Phase 3 `cloudbuild.yaml`, extend `--set-env-vars`:
`FRONTEND_BASE_URL=https://staging.carddroper.com` (alongside the existing
`SENDGRID_SANDBOX=false,FROM_EMAIL=...,FROM_NAME=...`).

---

### D-6 — Local dev regresses: no-key fallback removes the clickable link (severity: medium)

**`doc/operations/development.md`:14** says: *"the backend will log verification
links to stdout when SendGrid isn't configured."* That is the current behavior
and the exact path a developer running `docker-compose up` without a SendGrid
key uses to grab a verify link.

**0010 F-2 fix** restricts the no-key fallback log to
`{template, to_hash, mock_message_id}` — no link, no token, no recipient.

**Security rationale (F-2 audit context):** token URLs in logs are bad in
staging/prod because Cloud Logging ingests stdout and anyone with log-read
access can harvest valid auth tokens.

**Local dev is different:** logs go to the developer's terminal only. The
exact-same fallback loses a real local-dev affordance for a threat that doesn't
apply on localhost.

**Fix before dispatch:** split the no-key fallback by environment. Phase 0
brief should say:

> In the no-key fallback, log fields:
> - Always: `{event, template, to_hash, mock_message_id}`.
> - Additionally, IF `settings.APP_ENV == "dev"`: a `dev_preview_url` field
>   reconstructed from `dynamic_template_data` (e.g. `verify_url` /
>   `reset_url` / `change_url`, whichever applies). Never include in
>   staging/prod log output.

Update `doc/operations/development.md`:14 to point to the `dev_preview_url`
field name (doc-hygiene follow-up, can land with the implementation commit).

---

### D-7 — `cloudbuild.yaml` uses two secret-mount patterns; 0010 compounds this (severity: low)

**Current state:** step 3 (migrate) uses the root-level `availableSecrets:` +
`secretEnv:` pattern for `MIGRATION_DATABASE_URL`. Step 4 (deploy) uses inline
`--set-secrets=` on `gcloud run deploy`. Two patterns in one file.

0010 Phase 3 extends the step-4 inline pattern, which is internally consistent
with the deploy step but does nothing to unify. Fine for 0010 — not its job
— but flagging for future ops cleanup.

**No fix required before dispatch.** Note for a later hygiene pass.

---

### D-8 — `doc/architecture/overview.md` stale Python version (severity: nit, not 0010's concern)

Line 17 says `Python 3.12`. `backend/Dockerfile` uses `python:3.11-slim` per
the backend audit, and `PLAN.md` §4 says 3.11. Drift is in the architecture
doc, not the code. Not 0010's fix — flagging to whoever next updates
`doc/architecture/overview.md`.

---

### D-9 — `support@carddroper.com` inbox doesn't exist (severity: info, PLAN.md §11)

The canary template copy (per `auth.md`) directs users to
`support@carddroper.com` for account-takeover reports. MX routing for that
address is in `PLAN.md` §11 open items ("MX / mail receiving for `support@`,
`privacy@`, `legal@carddroper.com`"). Any recipient of a canary email who
replies or emails support@ today gets a bounce.

**Not 0010's concern** — pre-launch operational punch-list item. Flagging so
it doesn't get lost, and so the implementing agent doesn't think the canary
template copy needs to change to a working address.

---

## Cross-doc consistency check

- `PLAN.md §10.5` (email layer) → consistent with 0010 once D-2 through D-5 land.
- `systems/auth.md` §Email change step 5 → only the canary template (D-2) and
  URL construction (D-5) are missing.
- `architecture/overview.md` → high-level shape matches (FastAPI → SendGrid);
  no contradictions.
- `operations/development.md`:14 → regressed by F-2 fix (D-6).
- `operations/environments.md`, `operations/deployment.md` → no SendGrid
  specifics to conflict with.

---

## Summary

| Severity | Count | IDs |
|---|---|---|
| HIGH (scope-breaking if not patched before dispatch) | 4 | D-2, D-3, D-4, D-5 |
| Medium | 2 | D-1, D-6 |
| Low / nit / info | 3 | D-7, D-8, D-9 |

**Recommendation:** Patch 0010 to address D-1 through D-6 before dispatching
Phase 0. D-7 through D-9 are doc-hygiene / pre-launch ops and do not block
implementation. Agents' code-layer audits (running in parallel) may surface
additional drift that consolidates into the same patch pass.
