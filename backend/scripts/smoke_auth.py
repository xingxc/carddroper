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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the auth golden path against staging.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    slug = uuid.uuid4().hex[:8]
    email = f"smoke+auth-{slug}@carddroper.test"

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

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # ------------------------------------------------------------------
        # 3. GET /auth/me
        # ------------------------------------------------------------------
        resp = client.get(f"{base}/auth/me", headers=auth_headers)
        if resp.status_code != 200:
            _fail(f"/auth/me returned {resp.status_code}: {resp.text[:200]}")

        me_body = resp.json()
        returned_email = me_body.get("email", "")
        if returned_email.lower() != email.lower():
            _fail(f"/auth/me email mismatch: expected {email!r}, got {returned_email!r}")

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
