---
id: 0018
title: chassis-hardening audit — find missing validators, grow chassis-contract.md
status: open (placeholder, scheduled after 0016/0017)
priority: medium
found_by: 0015.5 landing the first chassis contract entry (CORS) + Option C coupling rule in CLAUDE.md
---

## Context

0015.5 established the pattern: `doc/operations/chassis-contract.md` lists every chassis invariant 1:1 with its enforcement (pydantic validator, middleware check, etc.). `CLAUDE.md` requires new validators to land with contract entries in the same commit.

That rule governs **new** validators added from here forward. But the chassis already has settings where an invariant is implicit but not enforced. A deliberate pass is needed to find them and either:
- Add the validator + contract entry, OR
- Note explicitly why the invariant doesn't warrant enforcement (documented exclusion).

This ticket is that deliberate pass.

## Candidate areas to audit (non-exhaustive — the audit may surface others)

Each candidate is a hypothesis; the audit confirms or rejects based on actual risk to adopters.

- **`JWT_SECRET`** — should have a minimum-length check (e.g. ≥32 chars) and refuse empty string. Current state: no validator. Adopter running chassis with a weak or missing secret would have working-but-insecure auth.
- **`SENDGRID_API_KEY`** — required when `SENDGRID_SANDBOX=false`. Currently the chassis just logs a fallback `dev_preview_url` if the key is missing, which is correct for dev but would silently fail to deliver production emails.
- **`DATABASE_URL`** — required in prod; `asyncpg` driver prefix expected. Currently defaults to a local postgres URL; no check that it's been overridden in prod.
- **`FROM_EMAIL` / `FROM_NAME`** — required when emails are wired. Today both have plausible defaults; prod misconfiguration (e.g. unresolvable `@example.com`) would cause SendGrid failures that look like infra issues.
- **`SENDGRID_TEMPLATE_*` IDs** — all required when sandbox is off. Missing any would crash at first send attempt rather than at boot.
- **`JWT_ISSUER` / `JWT_AUDIENCE`** — required; refuse empty string. Tokens minted without these would be rejected by the decoder, but the error happens at first auth attempt.
- **Cookie `secure` flag** — should be true in prod (non-localhost). Today's middleware likely handles this; confirm.
- **Rate-limit settings** (`RESEND_VERIFICATION_RATE_LIMIT` etc.) — sensible defaults exist; likely low-value to add validators, but flag any that must be non-zero.

## Approach

1. Read `backend/app/config.py` end-to-end with the audit hat on.
2. For each field, classify: **must-be-valid-or-crash** (validator needed), **has-a-safe-default** (no action), **purely-product-tunable** (not in contract).
3. For each must-be-valid-or-crash field, write a validator + contract entry in the same commit (per the coupling rule).
4. Batch the additions into 1–3 commits depending on cohesion (e.g. "auth-side validators" commit, "email-side validators" commit).
5. For any field audited and deliberately left un-validated, add a one-line comment in `config.py` explaining why (prevents future auditors from re-auditing the same ground).

## Out of scope

- New chassis settings or features. The audit is pure hardening.
- Frontend settings audit. If the frontend has a chassis-contract equivalent later, that's its own audit.
- Running the audit against adopter projects. Adopters inherit the hardening for free once chassis is updated.

## Scheduling

Blocked on: 0015 Phase 2 completion (user-owned), 0015.5 landing, then follow-up work on 0016 (forgot/reset) and 0017 (change-email). Those tickets will themselves add new validators — letting them land first means this audit covers the whole auth surface in one pass instead of piecemeal.

## Report

When executed:
- Full field-by-field audit table (name, classification, action taken).
- Diffs of `config.py` (validators added) and `chassis-contract.md` (entries added).
- Any deliberate un-validated fields with the reasoning comment added in `config.py`.
- `pytest` + `ruff` summary lines.
- No staging deploy needed if the audit only adds validators with safe current defaults; if a new invariant causes staging to fail at startup, pause and dispatch an env-var fix first.

## Resolution

*(filled in on close)*
