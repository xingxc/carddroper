# Issues

Lightweight tracking for bugs, tech debt, and deferred decisions. One file per ticket: `<id>-<slug>.md`, zero-padded IDs.

## Ticket frontmatter

```yaml
id: 0001
title: short headline
status: open | in_progress | resolved | wontfix
priority: low | medium | high
found_by: source (e.g., "backend-builder audit 2026-04-19")
```

## Workflow

1. Orchestrator creates the ticket with clear acceptance criteria.
2. Agent is dispatched with the ticket ID; it reads the full file and executes the acceptance.
3. Agent reports back. Agent does NOT modify the ticket file.
4. Orchestrator verifies, flips `status` to `resolved`, appends a Resolution note.

## Ticket sections (template)

Every ticket file should have these sections in order:

- **Context** — one paragraph: what problem this solves, what doc(s) it's grounded in, what's already done.
- **Acceptance** — numbered list of concrete deliverables. Each item is observable (file exists, function signature matches, test passes).
- **Verification** — required. Two sub-bullets:
  - **Automated checks:** the commands the agent runs to prove correctness (e.g., `pytest tests/ -k auth`, `tsc --noEmit`, `npm run lint`, `npm run build`).
  - **Functional smoke:** the end-to-end check that proves the feature *works*, not just that the code compiles. Examples: `curl localhost:8000/auth/me` returns 401 when unauthenticated; `curl localhost:3000` SSR HTML contains "Carddroper"; webhook signature verification rejects a tampered payload. If the smoke can only be run by the user (visual UI check, Stripe live mode), name it explicitly so the orchestrator surfaces it after dispatch.
- **Out of scope** — what the agent should NOT touch, even if tempting. Prevents scope creep.
- **Report** — what the agent's reply must include (files touched, deps added, deviations).
- **Resolution** — added by the orchestrator on close, not the agent.

The Verification section exists because "tsc passes" and "lint passes" are necessary but not sufficient. We learned this on ticket 0004 — the dev server started but we hadn't confirmed the page actually rendered. Bake the smoke into the ticket so no agent can return "done" without exercising the feature.

## Index

| ID | Title | Status | Priority |
|---|---|---|---|
| 0001 | JWT exp datetime convention exception | resolved | low |
| 0002 | pytest-asyncio event_loop deprecation | resolved | medium |
| 0003 | passlib / Python 3.13 crypt removal | resolved | medium |
| 0004 | frontend scaffold — Next.js 16 + TS strict + Tailwind v4 + React Query | resolved | high |
| 0005 | docker-compose — Postgres + backend + frontend, one command up | resolved | high |
| 0006 | staging GCP foundation — project, IAM, Cloud SQL, AR, Secret Manager | resolved | high |
| 0007 | staging first deploy — cloudbuild.yaml, trigger, *.run.app verification | resolved | high |
| 0008 | staging custom domains — Cloudflare CNAMEs + Cloud Run domain mappings | resolved | high |
| 0009 | scaffold code audit — backend + frontend ground-truth inventory before v0.1.0 features | resolved | high |
| 0010 | SendGrid infrastructure — hardened send_email() helper + staging secret wiring | resolved | high |
| 0011 | backend hardening — global 500 exception handler + JWT iss/aud claims | resolved | high |
| 0012 | Dockerfile hardening — non-root + multi-stage + HEALTHCHECK + public/ cleanup | resolved | medium |
| 0013 | testing methodology doc + coverage audit + backfill | resolved | high |
| 0014 | pre-0015 cleanup — backend + frontend hygiene batch from audit 2026-04-21 | resolved | medium |
| 0014.5 | pre-0015 micro-cleanup — viewport, tsconfig strict, cloudbuild sleep, dockerfile-copy doc | resolved | medium |
| 0015 | email verification flow — register, login, verify-email pages + auth foundations | resolved | high |
| 0015.5 | staging CORS unblock + chassis boot-time guard + chassis contract doc | resolved | high |
| 0015.6 | cross-subdomain cookie domain chassis invariant + verify-email-sent Resend guard | resolved | high |
| 0015.7 | verify-email clears dead cookies (reverted — wrong direction) | superseded by 0015.8 | high |
| 0015.8 | verify-email is a capability toggle, not a session reset — supersedes 0015.7 | resolved | high |
| 0016 | forgot-password + reset-password pages + /login link + reset ghost-session fix | resolved | high |
| 0016.1 | rate-limiter keys on Cloud Run GFE IP, not client IP — X-Forwarded-For resolution | resolved | high |
| 0016.2 | (app)/ auto-redirect to /login on cross-device stale-cookie ghost state | resolved | medium |
| 0016.3 | 401 interceptor calls /auth/logout to clear stale cookies — escapes proxy loop | resolved | high |
| 0016.4 | /login auto-redirects to /app on silent-refresh re-auth — proxy-symmetric redirect pair | resolved | medium |
| 0016.5 | chassis blurry LoadingScreen — unify pre-decision loading states | resolved | medium |
| 0016.6 | proactive access-token refresh — OAuth 2.0 expires_in + 80%-TTL scheduler | resolved | medium |
| 0016.7 | drop redundant logout client-state reset — hard-reload supersedes in-tree cache invalidation | resolved | low |
| 0016.8 | distinct 401 error codes — stop wasted /auth/logout cleanup POSTs on anonymous + post-logout loads | resolved | medium |
| 0017 | change-email retroactive audit — verify spec compliance + close any gaps | resolved | high |
| 0017.1 | change-email frontend pages — request form + confirm landing + ProfileMenu link | resolved | medium |
| 0018 | chassis-hardening audit — find missing validators, grow chassis-contract.md | resolved | medium |
| 0019 | email deliverability — SendGrid Sender Authentication + SPF / DKIM / DMARC | resolved | high |
| 0019.1 | email template polish — Subject lines + branded HTML + drop unsubscribe block (5 templates) | resolved | medium |
| 0024 | subscribe + lifecycle handlers (chassis) — /billing/subscribe + /billing/setup-intent + GET subscription + 5 webhook handlers + Stripe Elements SubscribeForm | resolved | medium |
| 0024.1 | chassis tier resolution from Stripe — GET /billing/tiers + format_price + useTiers + SubscribeForm prop change | resolved | medium |
| 0024.2 | subscription grants opt-in (BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER) + fix sub.items extraction bug | resolved | medium |
| 0024.3 | paid actions opt-out of verified-gate (BILLING_REQUIRE_VERIFIED, default False) | resolved | medium |
| 0024.4 | subscriptions.current_period_start/end extraction regression (introduced by 0024.2) | resolved | medium |
| 0024.5 | webhook handlers stop overwriting period fields with NULL — architectural fix (Path B) | resolved | medium |
| 0024.6 | subscribe idempotency key collides on retry with different payment_method_id (3DS, decline-retry) | resolved | medium |
| 0024.7 | webhook handlers stop overwriting subscriptions.grant_micros — apply Path B to grant_micros | resolved | medium |
| 0024.8 | subscribe endpoint stores 0 in grant_micros when flag=false (strict flag-gate, regardless of metadata) | resolved | medium |
| 0024.9 | subscribe failure recovery — distinguish 3DS from decline; sync-cancel terminal failures; clean up incomplete rows | resolved | medium |
| 0024.10 | setup-intent idempotency replay during 0024.9 soft-reset; defensive handleSubmit catch | resolved | medium |
| 0024.11 | couple subscription_grant to invoice.paid (subscription_create), not customer.subscription.created — fixes 3DS-fail phantom grant | resolved | high |
| 0024.12 | basil API moved invoice.subscription field; stripe_extractors module + 3DS-fail UX symmetry with decline | resolved | high |
| 0024.13 | handle_subscription_created INSERT path must flag-gate grant_micros; adds cross-writer audit discipline (Q3.5) | resolved | medium-high |
| 0024.14 | renewal cycle test-clock verification — script + fixture template + setup docs | resolved | medium |
| 0020 | legal acceptance — ToS checkbox on /register + /legal/terms + /legal/privacy static pages | open | high |
| 0021 | Stripe foundation (chassis) — balance ledger, billing primitives, webhook skeleton | resolved | medium |
| 0022 | app-shell refactor (chassis) — left-rail sidebar + profile popover menu | resolved | medium |
| 0023 | PAYG topup (chassis) — /billing/topup + /billing/balance + PaymentElement TopupForm | resolved | medium |
| 0023.1 | test isolation fixes — Kind-1 patch + Kind-2 skipif for billing tests | resolved | low |
| 0023.2 | webhook dedup concurrency — atomic INSERT … ON CONFLICT replaces SELECT-then-INSERT | resolved | medium |
