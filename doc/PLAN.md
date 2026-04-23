# Carddroper — Build Plan (decision log)

> Working agreement before we start writing code. This doc records *decisions*. Details of each system live under `doc/architecture`, `doc/systems`, `doc/operations`, and `doc/legal`.
>
> Last updated: 2026-04-19

---

## 1. What carddroper is

A web + (later) mobile application in the spirit of Paperless Post — users compose and send communications to recipients. The exact feature set is deliberately out of scope for this plan. **Features and database schema come later.** This plan covers only architecture, stack, authentication, and payments — the reusable substrate that doesn't change when features land.

Brand: **carddroper** (placeholder — final name/domain TBD). We'll use `carddroper-*` for GCP project IDs, Artifact Registry repos, and Cloud Run service names.

---

## 2. Scope of v1 (this plan)

In scope:
- Auth: email + password, JWT access + refresh tokens, email verification, password reset, rate limits.
- Payments: **usage-based (pay-as-you-go credits)** as the primary model, with **optional subscription** tiers that include quota. Signup is free and does not require a subscription or payment method.
- Local + staging + prod environments on Google Cloud.
- Web frontend (Next.js). Backend designed to serve future mobile clients but `/mobile` is not built in v1.
- Documentation substrate (this file + subfolders).
- Legal pages (draft ToS + Privacy Policy, to be reviewed by counsel before launch).

Out of scope (until the feature plan replaces this one):
- Any domain features (composing, sending, recipients, templates, media).
- Any database tables beyond `users`, `refresh_tokens`, `email_verifications`, `subscriptions`, `credit_ledger`, `stripe_events`.
- Mobile apps.
- Social login.
- Push notifications.
- Admin dashboard / internal tooling.

---

## 3. Architecture at a glance

```
           Web (Next.js 15)                  (mobile later)
                 │ cookies+Bearer                 │ Bearer
                 └───────────┬────────────────────┘
                             ▼
                    ┌──────────────────┐
                    │  FastAPI API     │   ← Cloud Run
                    │  Python 3.11     │
                    └────┬─────────┬───┘
                         │         │
                ┌────────▼───┐  ┌──▼────────┐
                │ Postgres   │  │  Stripe   │
                │ Cloud SQL  │  │  SendGrid │
                └────────────┘  └───────────┘
```

Full detail: [architecture/overview.md](architecture/overview.md).

---

## 4. Stack (decisions)

| Layer | Choice | Why in one line |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2.0 async + asyncpg | Proven in foodapp, async-native, Pydantic validation, clean ORM. |
| Database | Postgres 16 (local) / Cloud SQL 16 (prod) | Strong relational fit, same engine local↔prod, Google-managed backups. |
| Migrations | Alembic | Standard. Fresh chain — we do *not* carry foodapp migrations forward. |
| Auth tokens | JWT (HS256) + rotating refresh tokens stored as SHA-256 hashes | Same pattern as foodapp; mobile-friendly via Bearer + refresh-by-body. |
| Password hashing | bcrypt (used directly) | Industry standard. passlib dropped in ticket 0003 for Python 3.13 compat. |
| Rate limiting | slowapi | Per-IP on auth mutations. |
| Email | SendGrid (transactional: verify, reset, receipts) | Cheap, reliable, existing foodapp template. |
| Frontend (web) | Next.js 16 + React 19 + TypeScript 5 strict + TailwindCSS v4 + React Query v5 | Greenfield install at scaffold time (ticket 0004); ports `auth.tsx` + `api.ts` shape from foodapp. |
| Payments | Stripe — Payment Intents for PAYG top-ups, Subscriptions for optional tiers, webhooks for sync | See [systems/payments.md](systems/payments.md). |
| Hosting | Google Cloud Run (backend + frontend), Cloud SQL, Artifact Registry, Secret Manager, Cloud Build | Same as foodapp; parent account already set up. |
| Version control | GitHub, monorepo layout | Single source of truth. Layered `.gitignore`: root for cross-cutting (OS junk, `.env*`, IDE), tree-local under `backend/` and `frontend/` for language-specific paths. |
| Local dev | Docker Compose (Postgres + backend + frontend) | One command to start everything. |

Full detail: [architecture/tech-stack.md](architecture/tech-stack.md).

---

## 5. Environments (decision)

**Three environments, three configurations, two GCP projects.**

| Env | Runs on | Stripe keys | Deploy trigger |
|---|---|---|---|
| **dev** | Your Mac via `docker-compose up` | Test keys | Manual `docker-compose up` |
| **staging** | GCP project `carddroper-staging` | Test keys | Push to `main` |
| **prod** | GCP project `carddroper-prod` | Live keys | Push a `v*` git tag |

Why two GCP projects: full isolation of database, secrets, IAM, and Stripe keys. Catches the class of bugs that only appear in a cloud deployment (Unix socket paths, IAM bindings, domain mapping, secret access) without exposing prod users.

Why a git tag for prod (not branch push): "what's in prod" is pinned to an immutable tag, not a moving branch. Release becomes an explicit action (`git tag v1.2.3 && git push --tags`) rather than a side effect of merging.

**Domain layout (DNS managed at Cloudflare):**
- Prod: `carddroper.com` (frontend) + `api.carddroper.com` (backend)
- Staging: `staging.carddroper.com` (frontend) + `api.staging.carddroper.com` (backend)

All four are CNAMEs to `ghs.googlehosted.com`. Proxy status starts DNS-only ("grey cloud"); flip to proxy mode once deployment is stable.

Full detail: [operations/environments.md](operations/environments.md).

---

## 6. Authentication (decision)

Email + password only for v1. Lifts the JWT + refresh-token design from foodapp, with these adjustments:

1. **Email verification required.** New column `users.verified_at`. `POST /auth/register` sends a verification email with a signed token. `POST /auth/verify-email` marks the user verified. Unverified users can log in (read-only access to account pages) but cannot make purchases or use credits.
2. **Mobile-friendly refresh.** `/auth/refresh` and `/auth/logout` accept the refresh token either as a cookie (web) or in the JSON body (mobile). No change to security model — the server still hashes-and-looks-up.
3. **No `Restaurant` entity.** Drop the foodapp coupling; carddroper users stand alone.
4. **No subscription gate on signup.** foodapp requires a payment method at register; carddroper does not. Sign up → verify email → optionally top up credits or subscribe.
5. **7-day soft cap on unverified accounts.** Days 0–6: login works, paid actions blocked. Day 7 onward: account is **locked** — login still works but only `/verify-email`, `/resend-verification`, `/change-email`, `/auth/me`, `/auth/logout` are reachable; everything else returns 403. Day 30: a nightly sweep hard-deletes still-unverified accounts (releases the email for re-registration; aligns with Privacy Policy §4).
6. **Password policy.** Minimum 10 characters, no composition rules, checked against the HIBP k-anonymity API at register / reset / change. Applies identically across all password-setting endpoints. Fails open if HIBP is unreachable (bcrypt is still the primary defense).
7. **Per-account login lockout.** On top of per-IP rate limiting, 10 failed logins on the same email within a 15-minute window triggers a 15-minute lockout that returns "too many attempts" without checking the submitted password — prevents rotating-IP credential stuffing. Password reset still works during lockout.
8. **Email change flow.** Standard money-handling pattern: re-prompt current password → verification link to the **new** address → flip `users.email` + bump `token_version` → notification to the **old** address ("your email was changed"). The notification to the old address is the canary that detects silent account takeover.

Full detail: [systems/auth.md](systems/auth.md).

---

## 7. Payments (decision)

**Usage-based as primary, subscription as optional add-on.**

- On signup, we create a Stripe Customer immediately (no payment method, no charges).
- **PAYG (pay-as-you-go):** User buys credits via a Stripe Payment Intent. We track balance in a `credit_ledger` table (append-only; current balance = sum of entries). Each "send" action deducts credits. Simple, transparent, no surprises.
- **Subscription (optional):** User picks a tier (e.g. "100 sends/month for $X"). We track monthly-quota usage in the ledger. When quota is exhausted, overages automatically roll to PAYG credits at the same rate (or we block if they have no credits — configurable).
- Stripe webhooks drive state: `payment_intent.succeeded` → grant credits; `customer.subscription.*` → update tier; `invoice.paid` → reset monthly quota.

Why a credit ledger and not Stripe Metered Billing: user-visible balance, predictable charges, easy to debug, no end-of-month surprises. We can add true metered billing later if the business model needs it.

**UX split (matches foodapp's pattern).** Initial PAYG top-up and subscription signup use **embedded Stripe Elements** — card entry stays on carddroper.com, card data goes browser → Stripe directly (PCI SAQ A). Subscription management (cancel, change tier, update card, download invoices) redirects to the **Stripe Customer Portal** at `billing.stripe.com`. We build the custom cancellation flow only when we want retention logic — until then, the Portal handles it.

**Currency.** Credit ledger amounts are **integer USD cents**. Carddroper is USD-only in v1; international users pay USD, their issuing bank handles FX.

Full detail: [systems/payments.md](systems/payments.md).

---

## 8. Mobile strategy (decision)

**Deferred to v2.** When we build mobile, Expo / React Native is the starting point:
- Share types + API client with web (monorepo package).
- Single TS codebase for iOS + Android.
- EAS for builds; EAS Update for OTA JS patches.

The backend is built mobile-friendly from day one (Bearer token path, refresh via body, no cookie-only code paths). No new backend work is needed when mobile lands.

Native Swift/Kotlin is the second choice; only revisit if Expo becomes a real constraint.

---

## 9. Documentation structure

Mirrors foodapp's layout:

```
doc/
├── README.md                       ← index of all docs
├── PLAN.md                         ← this file (decision log)
├── architecture/
│   ├── overview.md                 ← system architecture, data flow
│   └── tech-stack.md               ← stack rationale
├── systems/
│   ├── auth.md                     ← auth design in depth
│   └── payments.md                 ← Stripe + credit ledger design
├── operations/
│   ├── development.md              ← local setup + day-to-day workflow
│   ├── environments.md             ← dev / staging / prod
│   └── deployment.md               ← GCP deployment playbook (filled during setup)
├── legal/
│   ├── terms-of-service.md         ← DRAFT, lawyer review required before launch
│   └── privacy-policy.md           ← DRAFT, lawyer review required before launch
└── reference/
    └── backend-api.md              ← OpenAPI-ish endpoint catalogue (filled as built)
```

---

## 10. Implementation order

1. **Scaffold `/backend`** — config, database, errors, logging, auth models, auth routes (register, login, refresh, logout, me, password change/forgot/reset, email verify/resend), user CRUD, Alembic, Dockerfile, tests. ✅ done
2. **Scaffold `/frontend`** — Next.js scaffold, App Router, TS strict, Tailwind v4, React Query provider, `lib/api.ts` helper. Auth pages come later. ✅ done (ticket 0004)
3. **Wire `docker-compose.yml`** — Postgres + backend + frontend, one command up. Validates the same container that runs locally is the one we'll ship.
4. **Stand up staging — minimum surface.** `carddroper-staging` GCP project, Cloud SQL, Secret Manager, Artifact Registry, Cloud Build trigger on `main`, domain mapping. Ship just `/health` (backend) + the `<h1>Carddroper</h1>` homepage (frontend) so the deployment path itself is proven before app surface grows. Catches IAM, Cloud SQL Unix-socket paths, secret access, domain mapping bugs while the surface is small enough to debug.
5. **Email layer** — harden `send_email()` helper (singleton client, async offload, tenacity retry, Dynamic Templates, structured logging), wire SendGrid domain auth + staging secrets, then the verification signed-token flow and verification frontend pages. Each merge to `main` auto-deploys to staging. ✅ done (tickets 0015, 0015.5, 0015.6, 0015.8 — email-verification flow end-to-end; 0015.7 superseded)
6. **Stripe layer** — create Customer on signup, PAYG Payment Intent endpoint, credit ledger, webhook handler, optional subscription endpoints. Auto-deploys to staging on merge.

   **Flipped from original order (2026-04-20).** Stripe was §10.5 and email was §10.6. Swap rationale: Stripe's PAYG receipt is an *email consumer* (credits-purchased template), and email verification gates paid actions (`require_verified` dep in `systems/auth.md`). Landing a trusted email layer first means Stripe work doesn't have to mock out or stub the email path. No scope change — both epics are still in v0.1.0.
7. **Stand up prod** — `carddroper-prod` GCP project, Cloud Build trigger on `v*` tags. No code change; just GCP setup mirroring staging.
8. **Smoke test end-to-end** on staging (auth + Stripe test mode + email), then tag and ship v0.1.0 to prod.

No features. No business logic. Just the bones, running on three environments, with docs that describe it all.

**Why staging moves to §10.4 (was §10.6):** cloud-only bugs (IAM, secret access, Unix-socket DB paths, domain mapping) are easiest to debug when the only thing on the deployment path is `/health`. If we wait until Stripe + email + auth are all on top, every staging failure has 5 possible causes. Push early, push small, then layer.

---

## 11. Open items (non-blocking)

Not blockers for starting §10 item 1. Resolve during implementation or at launch.

**Pricing / billing, to finalize during implementation:**
- Exact cents-per-credit value. Shape is locked: 1 credit = 1 send, priced in round USD cents. Number depends on SendGrid's per-email cost + our margin.
- PAYG refund policy — refund via Stripe + negative ledger entry, or disable refunds and handle case-by-case?
- Stripe Tax — enable day one (~$0.50/invoice, handles US sales tax + VAT) or stay tax-exempt until we cross a US state nexus?

**Operational, to finalize at / before launch:**
- Email DNS: SPF / DKIM / DMARC records at Cloudflare for SendGrid deliverability.
- MX / mail receiving for `support@`, `privacy@`, `legal@carddroper.com` (Cloudflare Email Routing is free).
- Error tracking tool (Sentry free tier vs Cloud Error Reporting).
- Uptime monitoring (Cloud Monitoring uptime checks are free).
- GCP budget alerts (propose $50/mo staging, $200/mo prod).
- GitHub branch protection on `main` + CI that runs tests on PR.
- Pre-commit hooks (ruff/black for Python, prettier/eslint for TS).
- Cloudflare proxy mode decision (start DNS-only; revisit after staging stable).

**Explicitly deferred to v2 (acknowledged, not a blocker):**
- Mobile apps (Expo / React Native).
- 2FA / MFA.
- Multi-currency pricing.
- Multi-region DR (v1 is us-west1 only).
- In-app billing management UI replacing the Stripe Customer Portal.
- Retention / save-a-customer flow in cancellation.
- Social login (Google, Apple).
- JWT secret rotation procedure.
- Paid load testing.
- GDPR "export my data" endpoint (handle manually until volume warrants).
