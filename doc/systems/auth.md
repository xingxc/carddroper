# Authentication

## Goals

1. Email + password signup, one user per email.
2. Short-lived access tokens, longer-lived refresh tokens, secure against XSS and CSRF.
3. Work identically for web (cookie-first) and mobile (Bearer-first, later).
4. Email verification required before any paid action.
5. Password reset via email with a single-use signed token.
6. Rate-limited everywhere to prevent abuse.

## Token design

**Access token (JWT, HS256)**
- Payload: `{ sub: user_id, tv: token_version, exp }`
- TTL: 15 minutes.
- Signed with `JWT_SECRET` (48-byte random, Secret Manager in prod).
- `tv` claim invalidates all outstanding tokens when the user changes password.

**Refresh token (opaque string, 48 bytes, url-safe)**
- TTL: 7 days.
- Stored as SHA-256 hash in `refresh_tokens` table with `user_id`, `expires_at`, `revoked_at`.
- The raw token is only ever in the client; the server can only verify by hash, not recover.
- Single-use by convention: `/auth/refresh` issues a new access token but does **not** rotate the refresh token (same as foodapp). Rotation can be added later if threat model warrants.

## Delivery to clients

**Web (cookies):**
- `access_token` — HttpOnly, Secure, SameSite=Lax, path `/`, 15-min max-age.
- `refresh_token` — HttpOnly, Secure, SameSite=Lax, path `/auth/refresh`, 7-day max-age.

**Mobile (body + Bearer, deferred to v2):**
- `POST /auth/login` response body includes both `access_token` and `refresh_token`.
- Client stores refresh token in Keychain (iOS) / EncryptedSharedPreferences (Android).
- Requests carry `Authorization: Bearer <access_token>`.
- `POST /auth/refresh` accepts refresh token in request body `{ "refresh_token": "..." }` in addition to cookies.
- `POST /auth/logout` same pattern.

The token extraction helper reads the cookie first, then falls back to the Authorization header. The body fallback for `/refresh` and `/logout` is a *route-specific* addition, not a global change.

## Email verification

**New column:** `users.verified_at TIMESTAMP NULL`.

**Flow:**
1. `POST /auth/register` creates the user with `verified_at = NULL`, creates a Stripe Customer, creates a signed verification JWT (TTL 24h, `purpose="verify"`), sends a SendGrid email with a link `https://carddroper.com/verify-email?token=<jwt>` (staging: `https://staging.carddroper.com/verify-email?token=<jwt>`).
2. User clicks the link. The frontend page calls `POST /auth/verify-email` with the token.
3. Backend decodes, checks `purpose=="verify"`, checks `user.verified_at is NULL`, sets `verified_at = now()`. The session is preserved — `verified_at` is a capability toggle (enabling paid actions post-soft-cap), not a security event. Reset-password and change-email remain session-invalidating (they bump `token_version` and revoke refresh tokens).
4. A resend endpoint `POST /auth/resend-verification` issues a new token, rate-limited to 3/hour.

**Enforcement:** a `require_verified` FastAPI dependency raises 403 on any endpoint that performs a paid action (credit purchase, subscription, send). Read-only account endpoints (`/auth/me`, `/profile`) remain accessible to unverified users so they can trigger the resend.

**UX:** after register, the frontend sends the user to `/verify-email-sent` which explains what's happening and provides a "resend" button. Until verified, paid actions in the UI are disabled with a "Please verify your email" banner.

**Soft cap (7-day lock, 30-day delete):**
- **Days 0–6:** login works, read-only account pages accessible, paid actions return 403. Reminder emails at days 1 and 4.
- **Day 6 email:** "your account will be locked tomorrow unless you verify."
- **Day 7 onward (locked):** a `require_not_locked` FastAPI dependency returns 403 on every **authenticated** route except `/auth/verify-email`, `/auth/resend-verification`, `/auth/change-email`, `/auth/me`, `/auth/logout`. Anonymous / unauthenticated routes (login, register, forgot-password, reset-password, refresh, etc.) are out of scope — the lock is a per-account control that only makes sense once a user is identified. Successful verification flips `verified_at` and instantly unlocks everything.
- **Day 29 email:** final warning before deletion.
- **Day 30:** a nightly sweep (Cloud Scheduler → Cloud Run job running `DELETE FROM users WHERE verified_at IS NULL AND created_at < now() - interval '30 days'`) hard-deletes still-unverified accounts. Releases the email for re-registration and aligns with the 30-day retention commitment in Privacy Policy §4. Cascades to `refresh_tokens`; no `subscriptions` rows should exist since paid actions are blocked.

## Password reset

Same design as foodapp:
1. `POST /auth/forgot-password` looks up the user by email. Always returns 200 to prevent enumeration. If user exists, issues a signed reset JWT (`purpose="reset"`, `tv=token_version`, TTL 15m) and emails the link `https://carddroper.com/reset-password?token=<jwt>` (staging: `https://staging.carddroper.com/reset-password?token=<jwt>`).
2. `GET /auth/validate-reset-token?token=...` lets the frontend show "invalid link" / "expired link" without consuming the token.
3. `POST /auth/reset-password` takes the token + new password. Validates `purpose`, `tv` still matches the user (single-use), updates `password_hash`, increments `token_version`, revokes all refresh tokens.

## Password policy

- **Minimum length: 10 characters.** No composition rules — NIST 800-63B guidance is that composition rules make passwords *weaker*, not stronger, by pushing users toward predictable substitutions (`P@ssw0rd1!`).
- **Breached-password check** via the HIBP k-anonymity API. Before accepting a new password at register / reset / change, we SHA-1 the password, send the first 5 hex chars to `api.pwnedpasswords.com/range/<5>`, and check whether the remaining 35 chars appear in the response. The password (and its full hash) never leave our server.
- **Fails open** if HIBP is unreachable — log a warning, accept the password. bcrypt is the primary defense; the breach check is belt-and-suspenders.
- Applied identically at all three entry points (register, reset, change) via a shared `validate_password(pw)` helper.

## Email change

Standard for-money-handling pattern — keeps the legitimate user in the loop and makes silent account takeover detectable.

1. User visits account settings, clicks "change email."
2. `POST /auth/change-email { current_password, new_email }` — backend verifies `current_password` (proves it's really them, not a hijacked session). Returns 400 if `new_email` is already in use.
3. Backend issues a signed JWT (`purpose="email_change"`, `tv=current_token_version`, `new_email=<...>`, TTL 1 h) and sends it to the **new** address as a verification link `https://carddroper.com/confirm-email-change?token=<jwt>` (staging: `https://staging.carddroper.com/confirm-email-change?token=<jwt>`). Old email continues to work until the link is clicked.
4. User clicks the link from the new inbox. Frontend calls `POST /auth/confirm-email-change { token }`. Backend:
   - Decodes, verifies `purpose` and that `tv` still matches the user (single-use within this token_version).
   - Updates `users.email = new_email`.
   - Increments `token_version` → all outstanding sessions (web + mobile) are invalidated; the user must log in again with the new email.
   - Revokes all refresh tokens.
5. Backend sends a **notification email to the old address**: *"Your email on carddroper was changed to `<new_email>` on `<date>`. If this wasn't you, contact `support@carddroper.com` immediately."* This is the canary — the legitimate owner always sees "your email is being changed" even if the session was hijacked.

We do not build a self-service "reverse this change" link in v1 — if a user reports an unauthorized change, support handles reversal manually. v2 ergonomic upgrade: a signed reversal link with 7-day TTL in the old-email notification.

## Proactive refresh

All four auth endpoints that issue or validate an access token now include `expires_in: int` (seconds) in their response bodies — `/auth/register`, `/auth/login`, `/auth/refresh`, and `/auth/me`. For `GET /auth/me` the body is an envelope `{ user, expires_in }` rather than a flat `UserResponse`, separating session state (TTL) from user state (id/email/verified_at). The field name follows OAuth 2.0 RFC 6749 §5.1 exactly, making the convention recognisable to any adopter familiar with OAuth providers.

The frontend `AuthProvider` runs a client-side scheduler: after each successful `/auth/me` or `/auth/refresh` response, a `setTimeout` fires at 80% of `expires_in` to call `POST /auth/refresh` proactively. On success, the timer self-reschedules using the new `expires_in` from the refresh response body. On any failure the scheduler stops — no false-logout, no reschedule. The next user action will 401 and fall through to the existing 0016.2 + 0016.3 + 0016.4 + 0016.5 chain, which handles cleanup and re-auth cleanly. The 80% threshold (20% buffer) matches the Auth0/Clerk industry default and absorbs network latency plus minor clock drift. The scheduler shares the `getRefreshPromise` dedup with the 401 interceptor, so a simultaneous proactive refresh and a 401 retry share one in-flight fetch. Tab backgrounding and device sleep may delay or skip the timer (OS constraint); the fallback chain handles those cases transparently.

## Rate limits

Two layers: per-IP rate limiting (slowapi) and per-account login lockout.

### Per-IP (slowapi)

- `/auth/register` — 3/min
- `/auth/login` — 5/min
- `/auth/refresh` — 10/min
- `/auth/logout` — 10/min
- `/auth/forgot-password` — 3/hour
- `/auth/resend-verification` — 3/hour
- `/auth/verify-email` — 10/min
- `/auth/change-email` — 3/hour
- `/auth/confirm-email-change` — 10/min

All limits configurable via `config.py`.

### Per-account login lockout

Per-IP alone doesn't stop rotating-IP credential stuffing. We additionally count failed logins per `email` address, regardless of source IP.

- **Threshold:** 10 failures on the same `email` within a 15-minute window.
- **Lockout:** for the next 15 minutes, every `POST /auth/login` with that email returns a generic "too many attempts, try again later" — **without checking the submitted password**. Short-circuits the bcrypt verify, so we don't leak timing information either.
- **Generic error message.** Same text we return for rate-limited responses. Hides whether the email is even registered.
- **Reset.** On a successful login we prune that email's recent failure rows.
- **Bypass.** The password-reset flow still works during lockout: a successful reset invalidates `token_version` and the lockout evaporates because the counter looks at recent failures, and a reset-driven password change is orthogonal.

**DoS caveat.** An attacker who knows your email can keep you locked out indefinitely by submitting bad passwords from any IP. Mitigations: (a) short 15-min lockout window, (b) generic error hides whether the email exists, (c) password reset remains functional.

Schema is in the Data model section below (`login_attempts`).

## Dependencies (FastAPI)

Trimmed from foodapp (no Restaurant, no subscription-gated version until needed):

- `get_current_user_optional(request, db) -> Optional[User]` — returns None on any failure.
- `get_current_user(request, db) -> User` — raises 401 on failure.
- `require_verified(user = Depends(get_current_user)) -> User` — 403 if `verified_at is None`.

Shared `_authenticate(request) -> (user_id, payload)` helper handles token extraction + decode + `tv` check, used by all three. This is the foodapp post-refactor pattern (already proven).

## Data model

```sql
users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    full_name       VARCHAR(255),
    verified_at     TIMESTAMP NULL,
    token_version   INT NOT NULL DEFAULT 0,
    stripe_customer_id VARCHAR(64) NULL,
    created_at      TIMESTAMP DEFAULT now(),
    updated_at      TIMESTAMP DEFAULT now()
);

refresh_tokens (
    id          SERIAL PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id),
    token_hash  VARCHAR(255) UNIQUE NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    revoked_at  TIMESTAMP NULL,
    created_at  TIMESTAMP DEFAULT now()
);

login_attempts (
    id            BIGSERIAL PRIMARY KEY,
    email         VARCHAR(255) NOT NULL,
    attempted_at  TIMESTAMP NOT NULL DEFAULT now(),
    ip            VARCHAR(45),           -- IPv4 or IPv6 text form
    success       BOOLEAN NOT NULL
);
CREATE INDEX ON login_attempts(email, attempted_at);
```

A nightly job prunes `login_attempts` older than 30 days — long enough for debugging and abuse forensics, short enough to keep the table bounded.

Exact schema is defined in the first Alembic migration; this block is documentation.

## Security notes

- Passwords are bcrypt. We never log the raw password or hash.
- JWT_SECRET rotation is a future exercise — it will require invalidating all outstanding tokens. Not in v1 scope.
- Cookies: Secure + HttpOnly + SameSite=Lax. CSRF is mitigated by SameSite for state-changing endpoints; we can add a double-submit token later if the threat model expands.
- Verification email tokens are short-lived (24h) and single-use (gated by `verified_at`).
- Reset tokens are single-use (gated by `token_version`) and 15-minute TTL.

## API error codes

Auth-chain 401 responses use three distinct error codes so clients can
react appropriately without second-guessing the cookie state:

- **`AUTHENTICATION_REQUIRED`** — no auth credentials were sent. The client
  should treat this as "not logged in" and prompt login without attempting
  any cookie cleanup (there's nothing to clean).
- **`INVALID_TOKEN`** — an access token was sent but rejected (bad signature,
  expired, `tv` mismatch after password reset / email change). The client
  should clear stale cookies via `POST /auth/logout` and prompt re-login.
  This covers the "ghost session" case where a new tab has stale cookies
  from a previous session but no in-memory session signal.
- **`UNAUTHORIZED`** — domain-level auth failure unrelated to cookie state:
  wrong login credentials, wrong current password on change-password or
  change-email, invalid reset/verify/confirm tokens on the public-token
  flows, refresh endpoint failures. Clients should surface a form-level
  error, not attempt cleanup.

The frontend `apiFetch` interceptor in `lib/api.ts` uses these codes to
decide whether to fire `attemptLogoutCleanup()` on a 401, avoiding wasted
round-trips on anonymous page loads and post-logout reloads while
preserving the ghost-session correctness guarantee from 0016.3.
