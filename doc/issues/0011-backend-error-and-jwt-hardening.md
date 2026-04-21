---
id: 0011
title: backend hardening — global 500 exception handler + JWT iss/aud claims
status: open
priority: high
found_by: ticket 0009 audit (backend F-1 high, backend F-3 medium)
---

## Context

Two audit findings from ticket 0009 that share the same backend-builder dispatch because both are error/auth-layer hardening touching `main.py`, `errors.py`, and `auth_service.py` in the same vicinity:

- **Backend F-1 (high, security + bug):** `main.py` registers exception handlers only for `AppError` and `RateLimitExceeded`. Any unhandled `Exception` falls through to FastAPI's default, which returns a non-standardised `{"detail": "Internal Server Error"}` and prints the raw traceback to stdout — which Cloud Run ingests into Cloud Logging. Traceback frames can carry DB URL fragments, partial SQL, stack-local tokens.
- **Backend F-3 (medium, security):** Every token (access, refresh-hash, reset, verify, email-change) is minted without `iss` or `aud` claims, and decoded without validating either. Single-service today so moot, but the moment we add a webhook / second service / CI signer with the same `JWT_SECRET`, cross-purpose token reuse becomes syntactically valid. Defence-in-depth we should already have.

Pairing rationale: both require touching `app/main.py` + the auth / error surface; single agent round-trip is cheaper than two. Each deliverable is independently verifiable — no coupling beyond the file set.

## Pre-requisites

- Ticket 0009 resolved (audit complete — this ticket is a direct child).

No other deps. Staging state unaffected; these are read-through improvements.

## Acceptance

### Phase 0: backend-builder — ship both fixes + tests (agent-executed)

Orchestrator dispatches **backend-builder** with this brief:

```
Task: Implement backend hardening per ticket 0011. Two deliverables, one commit.

====================================================================
Deliverable A — Global 500 exception handler (audit F-1)
====================================================================

  A1. Add an @app.exception_handler(Exception) in backend/app/main.py.
      Handler signature: async def internal_error_handler(request, exc).
      Response body shape (standardised — matches the existing AppError shape):
          {
              "error": {
                  "code": "INTERNAL_ERROR",
                  "message": "An unexpected error occurred.",
                  "request_id": "<value from request.state.request_id or None>"
              }
          }
      Status code: 500.
      Content-Type: application/json (FastAPI's JSONResponse handles this).

  A2. The handler MUST log the exception with full traceback at ERROR, INCLUDING
      any request_id (from the existing LoggingMiddleware on request.state), path,
      method. Log fields:
          {"event":"unhandled_exception", "request_id":..., "path":..., "method":...,
           "exc_type":<class name>, "exc_message":<str(exc)>}
      Use logger.exception() so the traceback attaches automatically.

  A3. The response MUST NOT include stack frames, exception class name, or
      exception message — only the generic "An unexpected error occurred." string
      and the request_id for correlation.

  A4. Register the handler AFTER the existing AppError and RateLimitExceeded
      handlers (FastAPI dispatches in specificity order regardless, but source
      ordering makes the catch-all role obvious to a reader).

  A5. Test (new file: backend/tests/test_exception_handler.py):
        - Add a temporary route that raises ValueError("boom") for the test only
          (via app.add_api_route in a fixture; not a permanent route).
        - Call the route with the test client.
        - Assert status 500.
        - Assert JSON body matches the shape above.
        - Assert body does NOT contain "boom", "ValueError", "Traceback".
        - Assert the logger received an "unhandled_exception" entry with the
          expected fields (use caplog or a logger capture fixture).

====================================================================
Deliverable B — JWT iss/aud claims (audit F-3)
====================================================================

  B1. Add two Settings fields (backend/app/config.py):
          JWT_ISSUER: str = "carddroper"
          JWT_AUDIENCE: str = "carddroper-api"

  B2. In backend/app/services/auth_service.py, every jwt.encode(...) call MUST
      include iss=settings.JWT_ISSUER and aud=settings.JWT_AUDIENCE in the payload.
      This applies to:
        - access tokens (the one used for /auth/me and protected routes)
        - purpose tokens (reset, verify, email_change) — the existing purpose
          claim stays; iss/aud are added alongside it.
      Refresh tokens are opaque, not JWTs — they do not need iss/aud.

  B3. Every jwt.decode(...) call MUST pass issuer=settings.JWT_ISSUER and
      audience=settings.JWT_AUDIENCE (both accepted positional or keyword per
      PyJWT signature). Decode sites the audit identified:
        - backend/app/dependencies.py:48  (get_current_user)
        - backend/app/services/auth_service.py:54  (decode for purpose tokens)
      Grep for every other jwt.decode call in the tree; all must pass both args.
      A missing iss or aud on decoded payload must raise (PyJWT raises
      InvalidAudienceError / InvalidIssuerError automatically when the args
      are passed — no custom logic needed).

  B4. Test (extend backend/tests/test_auth_flow.py or add a new
      backend/tests/test_jwt_claims.py):
        - Happy path: token minted by auth_service.create_access_token decodes
          successfully via auth_service.decode_token (or via a /auth/me call)
          and has iss=carddroper, aud=carddroper-api.
        - Wrong audience: manually mint a token with audience="carddroper-other"
          signed with the same JWT_SECRET. Call /auth/me with it. Assert 401
          with the AppError code the existing "invalid_token" path uses (match
          the project's existing shape; don't invent a new code).
        - Wrong issuer: same approach, iss="someone-else". Assert 401.
        - Missing aud: mint without audience. Assert 401.
        - Missing iss: mint without issuer. Assert 401.

====================================================================
Backwards compatibility
====================================================================

  - Tokens issued BEFORE this change exist in the wild only as long as users'
    browser sessions / refresh tokens remain. Access tokens expire in 15 min so
    they flush fast. Refresh tokens (7 day) are opaque and unaffected.
  - After merge + deploy, old access tokens will 401 (no iss/aud on payload).
    This is a transient ~15 min window where already-logged-in users get bumped
    to re-auth. Document this in the ticket Report; no migration needed.
  - Pending verify / reset / email-change tokens (max 24h lifespan) will also
    invalidate. Users in the middle of those flows will need a fresh link.
    Operational risk: low (staging only; no real users yet). Prod deploy: we'll
    pick a low-traffic window when that time comes.

====================================================================
Test + quality gate
====================================================================

  - pytest full suite must pass with zero regressions.
  - If ruff is configured, it must pass on the changed files.

Do NOT:
  - Touch email_service.py (that's ticket 0010).
  - Change the AppError / HTTPException shapes or codes.
  - Add iss/aud to refresh tokens (they're opaque, not JWTs).
  - Generate new JWT_SECRET values or rotate anything.

Report:
  - Files touched, one-line purpose each.
  - Settings fields added + defaults.
  - pytest output (pass count).
  - Any deviation from the brief.
```

### Phase 1: merge + verify on staging (user, CLI)

```bash
git checkout dev
git status                              # expect: backend changes from Phase 0
git add -A
git commit -m "backend: global 500 handler + JWT iss/aud claims (0011)"
git push origin dev
git checkout main
git merge --ff-only dev
git push origin main                    # triggers Cloud Build

gcloud builds list --region=us-west1 --limit=1
# Wait for SUCCESS, then:
curl -sSf https://api.staging.carddroper.com/health
# Expected: {"status":"ok","database":"connected"}
```

Any currently-authenticated browser session will bump to re-auth after the deploy — that's expected (pre-existing access tokens have no iss/aud). Log in again and confirm the session works.

### Phase 2: functional smoke (user, CLI)

Smoke the 500 handler from staging:

```bash
# There is no route that deliberately raises; we confirm via the happy-path
# shape staying consistent AND by looking at a 404 vs 500 body.

# Happy path
curl -sS https://api.staging.carddroper.com/health
# {"status":"ok","database":"connected"}

# 404 (unknown route) — should return FastAPI's default 404, not hit our handler
curl -sS -w "\nHTTP_STATUS=%{http_code}\n" \
    https://api.staging.carddroper.com/this-route-does-not-exist
# Expected: {"detail":"Not Found"} HTTP_STATUS=404

# 401 (protected route without auth) — should hit AppError handler, not 500
curl -sS -w "\nHTTP_STATUS=%{http_code}\n" https://api.staging.carddroper.com/auth/me
# Expected: existing AppError shape {"error":{"code":"unauthenticated",...}} HTTP_STATUS=401
```

To actually fire the 500 handler in staging, the cleanest path is to trust the
unit test + caplog assertions from Phase 0 and skip a live 500 trigger. If
confidence demands a live check: temporarily merge a `/_internal/boom` route
that raises, verify, revert in a follow-up commit — but this is optional and
the unit test covers it.

Smoke JWT iss/aud from staging:

```bash
# Mint a token with wrong audience using a Python one-liner (requires jwt
# library available locally, e.g. via the backend poetry env):
cd /Users/johnxing/mini/postapp/backend

JWT_SECRET=$(gcloud secrets versions access latest \
    --secret=carddroper-jwt-secret --project=carddroper-staging) \
  poetry run python -c "
import jwt, time
tok = jwt.encode(
    {'sub': '00000000-0000-0000-0000-000000000000', 'tv': 1,
     'iss': 'carddroper', 'aud': 'wrong-audience',
     'exp': int(time.time()) + 60},
    '$JWT_SECRET', algorithm='HS256')
print(tok)
"
# Take the printed token, hit /auth/me:
curl -sS -w "\nHTTP_STATUS=%{http_code}\n" \
    -H "Authorization: Bearer <PASTE_TOKEN>" \
    https://api.staging.carddroper.com/auth/me
# Expected: 401 with the project's existing invalid-token error shape.
```

## Verification

**Automated checks (backend-builder, reported in Phase 0):**

```bash
cd backend
poetry run pytest                                    # full suite, 0 regressions
poetry run pytest tests/test_exception_handler.py -v # new tests
poetry run pytest tests/test_jwt_claims.py -v        # new tests (or merged into test_auth_flow)
```

**Functional smoke (user, staging, Phase 2):**

- `/health` returns 200 JSON, shape unchanged.
- 404 on unknown route still returns FastAPI default shape (our handler is not greedy).
- `/auth/me` without credentials returns the existing AppError 401 shape, NOT the new `INTERNAL_ERROR` shape.
- Bearer token with wrong `aud` returns 401, not 500 or 200.
- Bearer token with wrong `iss` returns 401.
- Cloud Run log for a forced 500 (if exercised) includes `event=unhandled_exception`, exception class, and traceback fields — but the HTTP response body does NOT echo those.

## Out of scope

- Sentry / Error Reporting integration. That's a separate pre-launch operational ticket.
- Request-ID correlation across frontend and backend (the backend generates one; frontend doesn't yet log it).
- JWT secret rotation mechanism. Deferred per PLAN.md §11.
- Refresh token claim structure changes. Refresh tokens are opaque random tokens SHA-256 hashed at rest — not JWTs.
- Any `AppError` shape changes. The new 500 handler mimics the existing shape, not defines a new one.
- Backfilling existing tokens. The ~15-min re-auth flush on deploy is accepted.

## Report

Backend-builder:
- Files touched, one-line purpose.
- Settings fields added + defaults.
- pytest output (pass/fail + counts).
- Confirm no new top-level routes added (the 500 handler is not a route).
- Any deviation from brief.

User (Phase 2):
- Smoke output for the four curl checks.
- Staging deploy SUCCESS.
- Any session-bump surprises during the Phase 1 merge.

## Resolution

*(filled in by orchestrator on close)*
