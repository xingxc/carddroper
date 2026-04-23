#!/usr/bin/env python3
"""Smoke test: full auth golden path — register, login, /me, refresh, logout."""

import argparse
import sys
import uuid

import httpx

DEFAULT_BASE_URL = "https://api.staging.carddroper.com"
SMOKE_PASSWORD = "SmokeTest12345"


def _fail(message: str) -> None:
    print(f"SMOKE FAIL: auth — {message}", file=sys.stderr)
    sys.exit(1)


def _assert_cookie_domain(resp: httpx.Response, cookie_name: str, expected_domain: str) -> None:
    """Assert that a Set-Cookie header for cookie_name contains Domain=<expected_domain>."""
    set_cookie_headers = resp.headers.get_list("set-cookie")
    for header in set_cookie_headers:
        # Match cookie name at the start of the header value.
        if header.split("=", 1)[0].strip() == cookie_name:
            domain_attr = f"Domain={expected_domain}"
            if domain_attr.lower() not in header.lower():
                _fail(
                    f"Set-Cookie for {cookie_name!r} does not contain {domain_attr!r}. "
                    f"Got: {header!r}"
                )
            return
    _fail(f"No Set-Cookie header found for {cookie_name!r} in response.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the auth golden path against staging.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--expected-cookie-domain",
        default=None,
        metavar="DOMAIN",
        help=(
            "When set, assert that register and login responses include "
            "Domain=<DOMAIN> in the Set-Cookie headers for access_token and "
            "refresh_token. When not set, this assertion is skipped."
        ),
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    slug = uuid.uuid4().hex[:8]
    # Uses the real domain, not a .test / .invalid / .example TLD, because email-validator
    # rejects special-use TLDs. The smoke+ prefix lets a nightly sweep reap these.
    email = f"smoke+auth-{slug}@carddroper.com"

    with httpx.Client(timeout=10) as client:
        # ------------------------------------------------------------------
        # 1. Register
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/register",
            json={"email": email, "password": SMOKE_PASSWORD, "full_name": "Smoke Auth"},
        )
        if resp.status_code not in (200, 201):
            _fail(f"register returned {resp.status_code}: {resp.text[:200]}")

        reg_body = resp.json()
        if "access_token" not in reg_body:
            _fail(f"register response missing access_token: {reg_body}")
        if "refresh_token" not in reg_body:
            _fail(f"register response missing refresh_token: {reg_body}")

        # Optional: assert cookie domain on register response.
        if args.expected_cookie_domain:
            _assert_cookie_domain(resp, "access_token", args.expected_cookie_domain)
            _assert_cookie_domain(resp, "refresh_token", args.expected_cookie_domain)
            print(
                f"PASS: register Set-Cookie Domain={args.expected_cookie_domain} (access_token, refresh_token)"
            )

        # ------------------------------------------------------------------
        # 2. Login
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/login",
            json={"email": email, "password": SMOKE_PASSWORD},
        )
        if resp.status_code != 200:
            _fail(f"login returned {resp.status_code}: {resp.text[:200]}")

        login_body = resp.json()
        access_token = login_body.get("access_token")
        refresh_token = login_body.get("refresh_token")
        if not access_token:
            _fail(f"login response missing access_token: {login_body}")
        if not refresh_token:
            _fail(f"login response missing refresh_token: {login_body}")

        # Optional: assert cookie domain on login response.
        if args.expected_cookie_domain:
            _assert_cookie_domain(resp, "access_token", args.expected_cookie_domain)
            _assert_cookie_domain(resp, "refresh_token", args.expected_cookie_domain)
            print(
                f"PASS: login Set-Cookie Domain={args.expected_cookie_domain} (access_token, refresh_token)"
            )

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # ------------------------------------------------------------------
        # 3. GET /auth/me
        # ------------------------------------------------------------------
        resp = client.get(f"{base}/auth/me", headers=auth_headers)
        if resp.status_code != 200:
            _fail(f"/auth/me returned {resp.status_code}: {resp.text[:200]}")

        # /auth/me returns envelope {user, expires_in} per ticket 0016.6 (OAuth 2.0 shape).
        me_body = resp.json()
        user_obj = me_body.get("user") or {}
        returned_email = user_obj.get("email", "")
        if returned_email.lower() != email.lower():
            _fail(f"/auth/me email mismatch: expected {email!r}, got {returned_email!r}")
        if not isinstance(me_body.get("expires_in"), int) or me_body["expires_in"] <= 0:
            _fail(f"/auth/me envelope missing/invalid expires_in: got {me_body.get('expires_in')!r}")

        # ------------------------------------------------------------------
        # 4. POST /auth/refresh
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code != 200:
            _fail(f"/auth/refresh returned {resp.status_code}: {resp.text[:200]}")

        refresh_body = resp.json()
        new_access_token = refresh_body.get("access_token")
        if not new_access_token:
            _fail(f"/auth/refresh response missing access_token: {refresh_body}")

        # ------------------------------------------------------------------
        # 5. POST /auth/logout
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/logout",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code != 200:
            _fail(f"/auth/logout returned {resp.status_code}: {resp.text[:200]}")

    print("SMOKE OK: auth")


if __name__ == "__main__":
    main()
