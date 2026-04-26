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

---

## Invariant: `JWT_SECRET` non-empty and ≥ 32 characters

**Required:** yes — a missing or short JWT secret makes all tokens trivially forgeable.

**Purpose:** Every access and refresh token is signed with `JWT_SECRET` using HMAC-SHA256. An empty secret causes the library to accept any signature. A secret shorter than 32 characters can be brute-forced in seconds on commodity hardware. Both conditions allow an attacker to mint arbitrary tokens and authenticate as any user. Failing loudly at startup surfaces the misconfiguration before any user hits an auth endpoint.

**Error message on violation (empty):**

```
JWT misconfiguration: JWT_SECRET is empty.
Set JWT_SECRET to a random string of at least 32 characters.
Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Error message on violation (too short):**

```
JWT misconfiguration: JWT_SECRET is N characters; minimum is 32.
A short secret makes JWTs trivially forgeable.
Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_jwt_secret` (`@model_validator(mode="after")`).

**How to satisfy:**

- Generate a fresh secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"` (produces 64 URL-safe chars — well above the 32-char floor).
- Store in `backend/.env` as `JWT_SECRET=<value>` locally; in Cloud Run as a Secret Manager secret mapped to `JWT_SECRET`.

---

## Invariant: `JWT_ISSUER` and `JWT_AUDIENCE` non-empty

**Required:** yes — tokens minted with an empty issuer or audience are rejected by the decoder on every authenticated request.

**Purpose:** The JWT decoder validates `iss` and `aud` claims on every incoming access token. If either field is empty at Settings construction time, every token minted during the session will have an empty claim value, and every subsequent decode will fail with `JWTClaimsError`. This manifests as "all authenticated requests return 401" — indistinguishable from an expired token at the HTTP layer but covering 100% of requests. Failing at startup is cheaper than discovering the misconfiguration from a wall of 401 logs.

**Error message on violation (`JWT_ISSUER`):**

```
JWT misconfiguration: JWT_ISSUER is empty.
Set JWT_ISSUER to a non-empty string (e.g. 'carddroper').
Tokens minted with an empty issuer will be rejected by the decoder.
```

**Error message on violation (`JWT_AUDIENCE`):**

```
JWT misconfiguration: JWT_AUDIENCE is empty.
Set JWT_AUDIENCE to a non-empty string (e.g. 'carddroper-api').
Tokens minted with an empty audience will be rejected by the decoder.
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_jwt_issuer_audience` (`@model_validator(mode="after")`).

**How to satisfy:**

- Accept the defaults (`JWT_ISSUER=carddroper`, `JWT_AUDIENCE=carddroper-api`). These are safe and correct for any single-product deployment.
- If you rename either value, update both the `Settings` default **and** any existing tokens (or rotate all sessions) before deploying — existing tokens carry the old claim values and will fail validation until they expire.

---

## Invariant: `DATABASE_URL` uses `postgresql+asyncpg://` driver prefix

**Required:** yes — a URL without the asyncpg driver prefix causes a `Could not load backend` error at first DB operation rather than at startup.

**Purpose:** SQLAlchemy's async engine (`create_async_engine`) requires the `postgresql+asyncpg://` scheme. A plain `postgresql://` or `postgres://` URL (common in Heroku-style setups) silently parses but raises at first connection attempt. By that point the service is running and accepting requests, so the first DB-touching request fails with a 500 and every subsequent one does too — the failure looks like a DB outage rather than a config error. Catching it at startup makes the misconfiguration immediately obvious.

**Error message on violation:**

```
Database misconfiguration: DATABASE_URL must begin with 'postgresql+asyncpg://'.
Got: <first 40 chars of the supplied URL>
The async SQLAlchemy engine requires the asyncpg driver prefix.
Example: DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_database_url` (`@model_validator(mode="after")`).

**How to satisfy:**

- Local dev: `DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname`.
- Cloud Run: Secret Manager value must include the `+asyncpg` prefix — the most common mistake is copying a Heroku/Supabase connection string that uses `postgresql://` or `postgres://` without the driver segment.

---

## Invariant: `SENDGRID_TEMPLATE_*` IDs non-empty when `SENDGRID_SANDBOX=False` and `SENDGRID_API_KEY` is set

**Required:** only when `SENDGRID_SANDBOX=False` **and** `SENDGRID_API_KEY` is set (non-empty). When either condition is false, this check is skipped.

**Three modes:**

| `SENDGRID_SANDBOX` | `SENDGRID_API_KEY` | Check fires? |
|---|---|---|
| `True` | any | No — sandbox mode, no real delivery |
| `False` | empty | No — dev-preview mode, `send_email()` logs to stdout |
| `False` | non-empty | **Yes** — real delivery attempted; missing templates crash at first send |

**Purpose:** When real delivery is configured (`SENDGRID_SANDBOX=False` + real API key), `send_email()` looks up the template ID for each email type. A missing template ID raises `ValueError("SENDGRID_TEMPLATE_X is not configured")` inside the email service — a crash that only surfaces at the first time that email type is triggered (e.g., first user registration triggers `SENDGRID_TEMPLATE_VERIFY_EMAIL`). Failing at startup is cheaper and prevents silent partial delivery (some email types work, others crash).

**Error message on violation:**

```
SendGrid misconfiguration: SENDGRID_SANDBOX=False and SENDGRID_API_KEY is set, but the
following template IDs are not set: SENDGRID_TEMPLATE_VERIFY_EMAIL, ...
Missing template IDs cause send_email() to raise ValueError at the first email send
attempt rather than at startup.
Set each template ID, clear SENDGRID_API_KEY for dev-preview mode, or set SENDGRID_SANDBOX=True.
```

**Enforcement location:** `backend/app/config.py` — `Settings.validate_sendgrid_production` (`@model_validator(mode="after")`).

**How to satisfy:**

- **Local dev (no real email)**: leave `SENDGRID_API_KEY` empty OR set `SENDGRID_SANDBOX=True`. Template IDs may be empty in both cases.
- **Staging / production**: set all five template IDs (`SENDGRID_TEMPLATE_VERIFY_EMAIL`, `SENDGRID_TEMPLATE_RESET_PASSWORD`, `SENDGRID_TEMPLATE_CHANGE_EMAIL`, `SENDGRID_TEMPLATE_EMAIL_CHANGED`, `SENDGRID_TEMPLATE_CREDITS_PURCHASED`) as Secret Manager secrets.

---

## Invariant: every env var the chassis reads is a declared field on `Settings`

**Required:** yes — any env var present in the process environment (or `.env` file) that is not a declared field on `Settings` raises a `ValidationError` at startup.

**Purpose:** Unknown env vars silently dropped (the old `extra="ignore"` default) caused real adoption bugs. Example from 0016 setup: `FRONTEND_URL=https://staging.carddroper.com` was set in `.env` instead of the correct `FRONTEND_BASE_URL`; the app fell back to the `http://localhost:3000` default, verification links were malformed, and no error appeared until a smoke test caught it. `extra="forbid"` makes typos and mis-capitalized names immediately visible at startup rather than silently degrading behavior.

**Error message on violation:**

```
pydantic_core.core_schema.ValidationError: 1 validation error for Settings
<field_name>
  Extra inputs are not permitted [type=extra_forbidden, ...]
```

**Enforcement location:** `backend/app/config.py` — `SettingsConfigDict(extra="forbid")`.

**How to satisfy:**

- Before adding a new env var to any deployment config (`cloudbuild.yaml` `--set-env-vars` / `--set-secrets`, Cloud Run service YAML, `.env` files), confirm the field is declared on `Settings`.
- The inverse: before adding a new `Settings` field, decide which `.env.example` tier it belongs in (see `doc/operations/development.md §Env-var tiers`) and add it there.

---

## Invariant: token-version bump + cookie action (per-endpoint classification)

**Required:** yes — any endpoint that bumps `user.token_version` must take an explicit, deliberate cookie action, or document why no action is appropriate.

**Purpose:** `token_version` is the chassis's session-invalidation primitive. Bumping it without clearing or re-issuing cookies leaves stale cookies in the browser. Depending on which endpoint bumps the version, the user's active session becomes silently broken: subsequent authenticated requests return 401 (`INVALID_TOKEN`) because the access token's `tv` claim no longer matches the DB. The invariant prevents endpoints from bumping the version and forgetting the cookie housekeeping.

**Three valid actions after a token_version bump:**

1. **Re-issue** — call `_set_auth_cookies(response, new_access_tok, new_refresh_tok)`. The new cookies carry a freshly minted token with the updated `tv`. The session continues seamlessly.
2. **Clear** — call `_clear_auth_cookies(response)`. Forces the user to log in again. Use when the session should end (e.g., password reset from an unknown device, logout-equivalent flows).
3. **No session** — the endpoint is reached from a stateless context (e.g., via an email link, not an authenticated browser session). No cookie header is present on the request; no action needed. Document explicitly.

**Current endpoint classification (as of 0018):**

| Endpoint | Location | tv bump | Cookie action | Classification |
|---|---|---|---|---|
| `PUT /auth/password` (change-password) | `auth.py:444` | yes | `_set_auth_cookies` — new tokens issued | re-issues |
| `POST /auth/reset-password` | `auth.py:525` | yes | `_clear_auth_cookies` | clears |
| `POST /auth/verify-email` | `auth.py` | no | n/a | N/A — no bump (0015.8 removed it) |
| `POST /auth/confirm-email-change` | `auth.py:682` | yes | none | no session — reached via email link; session self-invalidates on next request via tv mismatch |

**Future endpoints (e.g., 0017 change-email `PUT /auth/email`):** must add a row to this table at PR time, per the `CLAUDE.md` chassis-coupling rule. Do not re-open 0018 — the rule applies at each new PR.

**Enforcement location:** `backend/app/config.py` does not enforce this at runtime. Enforcement is at PR-review time via this contract entry. The `CLAUDE.md` chassis-coupling rule requires any PR adding a `token_version += 1` line to confirm or update this table in the same commit.
