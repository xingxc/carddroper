# Chassis Contract

This document lists the invariants the chassis enforces at startup. Each entry corresponds 1:1 to a validator or middleware check in chassis code. Adopters: violating any invariant causes the service to refuse to start. Authors: do not add entries without matching enforcement.

---

## Invariant: `CORS_ORIGINS ⊇ {FRONTEND_BASE_URL}`

**Required:** yes — misconfiguration causes all browser preflights to fail silently.

**Purpose:** The backend uses `allow_credentials=True` in `CORSMiddleware`, which makes the CORS spec reject wildcard origins. Every browser request from the frontend must be preceded by a preflight that returns the exact frontend origin in `Access-Control-Allow-Origin`. If `FRONTEND_BASE_URL` is absent from `CORS_ORIGINS` (and no matching `CORS_ORIGIN_REGEX` is set), every preflight is rejected and the frontend cannot call the API — it looks like a server outage from the browser's perspective, but the backend logs show no error.

**Error message on violation:**

```
CORS misconfiguration: FRONTEND_BASE_URL=<url> is not in CORS_ORIGINS=<list>
and does not match CORS_ORIGIN_REGEX=<regex or "(unset)">.
A browser served from the frontend URL cannot call this API.
Set CORS_ORIGINS to include FRONTEND_BASE_URL (CSV) or CORS_ORIGIN_REGEX to match it.
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_cors_origins` (`@model_validator(mode="after")`).

**How to satisfy:**

- Common case (single environment): set `CORS_ORIGINS` to include `FRONTEND_BASE_URL` exactly. Example: `CORS_ORIGINS=https://staging.carddroper.com` when `FRONTEND_BASE_URL=https://staging.carddroper.com`.
- Multi-subdomain projects: set `CORS_ORIGIN_REGEX` to a pattern that matches `FRONTEND_BASE_URL`. The literal list check is then not required. Note: `CORS_ORIGIN_REGEX` is not currently wired into `CORSMiddleware.allow_origin_regex` in this version — set `CORS_ORIGINS` explicitly for each subdomain until that plumbing is added.

---

## Invariant: `FRONTEND_BASE_URL host ⊆ COOKIE_DOMAIN` (when `COOKIE_DOMAIN` is set)

**Required:** only when `COOKIE_DOMAIN` is set. When unset (the default), this check is skipped — correct for local dev on localhost.

**Purpose:** When frontend and backend live on different subdomains (e.g. `staging.carddroper.com` and `api.staging.carddroper.com`), auth cookies must be scoped to a common parent domain so both hosts can read them. If `COOKIE_DOMAIN` is set to a domain that does not cover `FRONTEND_BASE_URL`, the browser will not forward the cookies to the frontend host. The frontend proxy reads `request.cookies.has("access_token")` — with no cookie, it always sees "not logged in," making login appear broken while the backend logs show no error.

**Error message on violation:**

```
Cookie-domain misconfiguration: FRONTEND_BASE_URL host=<host> is not covered by COOKIE_DOMAIN=<cookie_domain>.
Browsers will not forward cookies scoped to COOKIE_DOMAIN to the frontend host, so the frontend proxy cannot gate auth routes.
Either leave COOKIE_DOMAIN unset (single-host deployments) or set it to a parent domain of FRONTEND_BASE_URL (e.g. ".example.com" for https://app.example.com).
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_cookie_domain` (`@model_validator(mode="after")`).

**How to satisfy:**

- **Single-host deployment** (frontend and backend on the same origin): leave `COOKIE_DOMAIN` unset. The check is skipped entirely.
- **Multi-subdomain deployment** (e.g. `app.X.com` + `api.X.com`): set `COOKIE_DOMAIN=.X.com`. Both hosts are under `.X.com`, so cookies are visible to both.
- **Cross-domain deployment** (e.g. `app.A.com` + `api.B.net`): cookie-based auth across fully different domains is not supported by this chassis. Use a same-origin reverse-proxy or a bearer-token strategy instead.
- **Caveat — backend host not validated:** the backend's own deployed host must also be under `COOKIE_DOMAIN` for the browser to accept the `Set-Cookie` response header. If it isn't, the browser silently rejects the `Set-Cookie`. The chassis does not validate the backend host (no `BACKEND_BASE_URL` setting exists today); adopters must verify this manually during deploy setup.

---

## Invariant: `STRIPE_SECRET_KEY` non-empty when `BILLING_ENABLED=true`

**Required:** only when `BILLING_ENABLED=true`. When `BILLING_ENABLED=false` (the default), this check is skipped — correct for any deployment not using Stripe.

**Purpose:** When `BILLING_ENABLED=true`, the chassis calls `stripe.Customer.create` at registration and validates webhook signatures. Both calls require a valid Stripe secret key. Without it, every Stripe API call raises `stripe.error.AuthenticationError`, making registration fail with a 500 and the webhook endpoint return errors. Failing loudly at startup surfaces the misconfiguration before any user hits it.

**Error message on violation:**

```
Stripe misconfiguration: BILLING_ENABLED=True but STRIPE_SECRET_KEY is not set.
Set STRIPE_SECRET_KEY to a valid Stripe secret key (sk_live_... or sk_test_...).
Required because BILLING_ENABLED=True enables Stripe Customer creation at registration.
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_stripe_secret_key` (`@model_validator(mode="after")`).

**How to satisfy:**

- **Billing disabled** (default): leave `BILLING_ENABLED=false`. The check is skipped entirely.
- **Billing enabled**: set `STRIPE_SECRET_KEY=sk_test_...` (test mode) or `sk_live_...` (production) in the environment. Obtain from the Stripe Dashboard → Developers → API keys.

---

## Invariant: `STRIPE_WEBHOOK_SECRET` non-empty when `BILLING_ENABLED=true`

**Required:** only when `BILLING_ENABLED=true`. When `BILLING_ENABLED=false` (the default), this check is skipped and the webhook endpoint is not mounted.

**Purpose:** When `BILLING_ENABLED=true`, the webhook endpoint at `POST /billing/webhook` is mounted and validates every inbound event signature using this secret. Without it, `stripe.Webhook.construct_event` raises immediately on every webhook POST, making the endpoint non-functional. All webhook-driven balance grants (topup, subscription) will silently fail.

**Error message on violation:**

```
Stripe misconfiguration: BILLING_ENABLED=True but STRIPE_WEBHOOK_SECRET is not set.
Set STRIPE_WEBHOOK_SECRET to the webhook signing secret (whsec_...).
Required because BILLING_ENABLED=True mounts POST /billing/webhook.
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_stripe_webhook_secret` (`@model_validator(mode="after")`).

**How to satisfy:**

- **Billing disabled** (default): leave `BILLING_ENABLED=false`. The check is skipped entirely.
- **Local dev with Stripe CLI**: run `stripe listen --forward-to localhost:8000/billing/webhook`. The CLI prints `whsec_...` on startup. Copy that value to `STRIPE_WEBHOOK_SECRET`.
- **Staging / production**: create a webhook endpoint in the Stripe Dashboard pointing to `https://<your-api-host>/billing/webhook`. Copy the signing secret shown under the endpoint's details.
