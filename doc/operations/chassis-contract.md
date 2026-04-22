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
- Multi-subdomain projects: set `CORS_ORIGIN_REGEX` to a pattern that matches `FRONTEND_BASE_URL`. The literal list check is then not required. Note: `CORS_ORIGIN_REGEX` is not currently wired into `CORSMiddleware.allow_origin_regex` — set `CORS_ORIGINS` explicitly for each subdomain until that plumbing is added.
