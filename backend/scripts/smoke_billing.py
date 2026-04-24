#!/usr/bin/env python3
"""Smoke test: billing balance endpoint golden path.

Steps:
1. Register a fresh smoke user.
2. Authenticate (use token from register response).
3. GET /billing/balance — assert {balance_micros: 0, formatted: "$0.00"}.
4. Cleanup: POST /auth/logout.

Note: topup is NOT smoked here — it requires Stripe test-mode infrastructure
(card input via Elements + Stripe CLI webhook forwarding). End-to-end topup
is validated in Phase 1 local testing.
"""

import argparse
import sys
import uuid

import httpx

DEFAULT_BASE_URL = "https://api.staging.carddroper.com"
SMOKE_PASSWORD = "SmokeTest12345"


def _fail(message: str) -> None:
    print(f"SMOKE FAIL: billing — {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the billing balance endpoint against staging.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    slug = uuid.uuid4().hex[:8]
    email = f"smoke+billing-{slug}@carddroper.com"

    with httpx.Client(timeout=10) as client:
        # ------------------------------------------------------------------
        # 1. Register
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/register",
            json={"email": email, "password": SMOKE_PASSWORD, "full_name": "Smoke Billing"},
        )
        if resp.status_code not in (200, 201):
            _fail(f"register returned {resp.status_code}: {resp.text[:200]}")

        reg_body = resp.json()
        access_token = reg_body.get("access_token")
        refresh_token = reg_body.get("refresh_token")
        if not access_token:
            _fail(f"register response missing access_token: {reg_body}")
        if not refresh_token:
            _fail(f"register response missing refresh_token: {reg_body}")

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # ------------------------------------------------------------------
        # 2. GET /billing/balance
        # ------------------------------------------------------------------
        resp = client.get(f"{base}/billing/balance", headers=auth_headers)
        if resp.status_code != 200:
            _fail(f"GET /billing/balance returned {resp.status_code}: {resp.text[:200]}")

        balance_body = resp.json()
        balance_micros = balance_body.get("balance_micros")
        formatted = balance_body.get("formatted")

        if balance_micros != 0:
            _fail(f"Expected balance_micros=0, got {balance_micros!r}")

        if formatted != "$0.00":
            _fail(f"Expected formatted='$0.00', got {formatted!r}")

        # ------------------------------------------------------------------
        # 3. Logout (cleanup)
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/logout",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code != 200:
            _fail(f"/auth/logout returned {resp.status_code}: {resp.text[:200]}")

    print("SMOKE OK: billing")


if __name__ == "__main__":
    main()
