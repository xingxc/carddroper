#!/usr/bin/env python3
"""Smoke test: email-verification flow — register, /auth/me (unverified), resend-verification.

Exercises the register → verify-email-sent → resend-verification API round-trip.

NOTE — verify-email token step (Option B):
  The actual POST /auth/verify-email call requires a signed JWT that is only ever
  delivered via email (SendGrid). This smoke cannot intercept a real inbox, and no
  dev/admin token-mint endpoint exists in the public API surface (adding one would
  be a security risk and is explicitly out of scope). The verify-email endpoint is
  therefore covered by the manual browser walkthrough documented in ticket 0015
  Phase 2, step 4. The remaining value of this script is:
    1. Preflight /health
    2. Register a fresh smoke user (asserts verified_at is null on response)
    3. GET /auth/me (asserts unverified state)
    4. POST /auth/resend-verification (asserts 200 + success message)
    5. Cleanup via POST /auth/logout

  The 3/hour rate-limit 429 path for resend-verification is intentionally NOT
  tested here. Triggering it would rate-limit the shared staging IP and could
  break concurrent smoke runs or manual verification attempts.
"""

import argparse
import sys
import uuid

import httpx

DEFAULT_BASE_URL = "https://api.staging.carddroper.com"
SMOKE_PASSWORD = "SmokeTest12345"

_passed = 0
_failed = 0


def _pass(label: str) -> None:
    global _passed
    _passed += 1
    print(f"  PASS: {label}")


def _fail(label: str, detail: str) -> None:
    global _failed
    _failed += 1
    print(f"  FAIL: {label} — {detail}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the email-verification flow against staging.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    slug = uuid.uuid4().hex[:8]
    # Uses the real domain (not .test / .invalid / .example) because email-validator
    # rejects special-use TLDs. The smoke+verify- prefix lets a nightly sweep reap these.
    email = f"smoke+verify-{slug}@carddroper.com"

    access_token: str = ""
    refresh_token: str = ""

    with httpx.Client(timeout=10) as client:
        # ------------------------------------------------------------------
        # 1. Preflight: GET /health
        # ------------------------------------------------------------------
        print("1. Preflight — GET /health")
        try:
            resp = client.get(f"{base}/health")
        except httpx.RequestError as exc:
            print(f"SMOKE FAIL: verify_email — request error on /health: {exc}", file=sys.stderr)
            sys.exit(1)

        if resp.status_code != 200:
            _fail("/health status", f"expected 200, got {resp.status_code}: {resp.text[:200]}")
        else:
            try:
                body = resp.json()
                if body.get("status") != "ok":
                    _fail("/health body", f"expected status='ok', got {body.get('status')!r}")
                else:
                    _pass("/health returned 200 with status=ok")
            except Exception as exc:
                _fail("/health JSON", f"could not parse body: {exc}")

        # ------------------------------------------------------------------
        # 2. Register a fresh smoke user
        # ------------------------------------------------------------------
        print(f"2. Register — POST /auth/register ({email})")
        try:
            resp = client.post(
                f"{base}/auth/register",
                json={
                    "email": email,
                    "password": SMOKE_PASSWORD,
                    "full_name": "Smoke VerifyEmail",
                },
            )
        except httpx.RequestError as exc:
            print(
                f"SMOKE FAIL: verify_email — request error on /auth/register: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

        if resp.status_code not in (200, 201):
            _fail(
                "register status",
                f"expected 200/201, got {resp.status_code}: {resp.text[:200]}",
            )
            # Cannot continue without a registered user.
            print(
                "SMOKE FAIL: verify_email — register step failed, cannot continue",
                file=sys.stderr,
            )
            sys.exit(1)

        reg_body = resp.json()

        # Assert access_token in body
        if "access_token" not in reg_body:
            _fail("register access_token", f"missing from response: {reg_body}")
        else:
            access_token = reg_body["access_token"]
            _pass("register response contains access_token")

        # Assert refresh_token in body
        if "refresh_token" not in reg_body:
            _fail("register refresh_token", f"missing from response: {reg_body}")
        else:
            refresh_token = reg_body["refresh_token"]
            _pass("register response contains refresh_token")

        # Assert verified_at is null
        user_data = reg_body.get("user", {})
        verified_at = user_data.get("verified_at")
        if verified_at is not None:
            _fail(
                "register verified_at",
                f"expected null, got {verified_at!r} — new user should not be verified",
            )
        else:
            _pass("register response has verified_at=null")

        if not access_token:
            print(
                "SMOKE FAIL: verify_email — no access_token available, cannot continue",
                file=sys.stderr,
            )
            sys.exit(1)

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # ------------------------------------------------------------------
        # 3. GET /auth/me — assert email matches and verified_at is null
        # ------------------------------------------------------------------
        print("3. GET /auth/me")
        try:
            resp = client.get(f"{base}/auth/me", headers=auth_headers)
        except httpx.RequestError as exc:
            _fail("/auth/me request", str(exc))
        else:
            if resp.status_code != 200:
                _fail("/auth/me status", f"expected 200, got {resp.status_code}: {resp.text[:200]}")
            else:
                # /auth/me returns envelope {user, expires_in} per ticket 0016.6 (OAuth 2.0 shape).
                me_body = resp.json()
                user_obj = me_body.get("user") or {}
                returned_email = user_obj.get("email", "")
                if returned_email.lower() != email.lower():
                    _fail(
                        "/auth/me email",
                        f"expected {email!r}, got {returned_email!r}",
                    )
                else:
                    _pass(f"/auth/me returned 200, email={returned_email!r}")

                expires_in = me_body.get("expires_in")
                if not isinstance(expires_in, int) or expires_in <= 0:
                    _fail(
                        "/auth/me envelope",
                        f"expected expires_in: positive int, got {expires_in!r}",
                    )
                else:
                    _pass(f"/auth/me returned expires_in={expires_in}s")

                me_verified = user_obj.get("verified_at")
                if me_verified is not None:
                    _fail(
                        "/auth/me verified_at",
                        f"expected null for new user, got {me_verified!r}",
                    )
                else:
                    _pass("/auth/me verified_at=null (unverified state confirmed)")

        # ------------------------------------------------------------------
        # 4. POST /auth/resend-verification
        #
        #    The 3/hour 429 path is intentionally not exercised here: triggering
        #    it against staging would rate-limit the shared IP and could break
        #    other smoke runs or manual flows running concurrently.
        # ------------------------------------------------------------------
        print("4. Resend verification — POST /auth/resend-verification")
        try:
            resp = client.post(f"{base}/auth/resend-verification", headers=auth_headers)
        except httpx.RequestError as exc:
            _fail("/auth/resend-verification request", str(exc))
        else:
            if resp.status_code != 200:
                _fail(
                    "/auth/resend-verification status",
                    f"expected 200, got {resp.status_code}: {resp.text[:200]}",
                )
            else:
                resend_body = resp.json()
                msg = resend_body.get("message", "")
                if not msg:
                    _fail(
                        "/auth/resend-verification message", f"empty message field: {resend_body}"
                    )
                else:
                    _pass(f"/auth/resend-verification returned 200, message={msg!r}")

        # ------------------------------------------------------------------
        # 5. Note on verify-email token step (Option B — manual-only)
        #
        #    POST /auth/verify-email requires a signed JWT delivered via email.
        #    No dev/admin token-mint endpoint exists in the public API surface.
        #    This step is covered by the manual browser walkthrough (ticket 0015
        #    Phase 2, step 4). Skipping the HTTP call here is intentional and safe.
        # ------------------------------------------------------------------
        print("5. verify-email token step — SKIPPED (manual-only; see ticket 0015 Phase 2, step 4)")

        # ------------------------------------------------------------------
        # 6. Cleanup — POST /auth/logout (best-effort)
        # ------------------------------------------------------------------
        print("6. Cleanup — POST /auth/logout")
        if refresh_token:
            try:
                resp = client.post(
                    f"{base}/auth/logout",
                    json={"refresh_token": refresh_token},
                )
                if resp.status_code == 200:
                    _pass("logout returned 200")
                else:
                    # Best-effort: don't fail the smoke if logout errors.
                    print(
                        f"  NOTE: logout returned {resp.status_code} (non-fatal): {resp.text[:200]}"
                    )
            except httpx.RequestError as exc:
                # Best-effort: don't fail the smoke if logout errors.
                print(f"  NOTE: logout request error (non-fatal): {exc}")
        else:
            print("  NOTE: no refresh_token captured, skipping logout")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\nResults: {_passed} passed, {_failed} failed")
    if _failed > 0:
        print(
            f"SMOKE FAIL: verify_email — {_failed} assertion(s) failed",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        "SMOKE OK: verify_email (partial — verify-token step is manual-only; "
        "see ticket 0015 Phase 2, step 4)"
    )


if __name__ == "__main__":
    main()
