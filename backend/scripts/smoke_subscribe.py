#!/usr/bin/env python3
"""Smoke test: subscription state endpoint golden path.

Steps:
1. Register a fresh smoke user.
2. Authenticate (use token from register response).
3. GET /billing/subscription — assert {has_subscription: false, tier_key: null, status: null}.
4. Cleanup: POST /auth/logout.

Note: full subscribe flow is NOT smoked here — it requires a configured Stripe
Price + payment method + Stripe CLI webhook forwarding. End-to-end subscribe is
validated in Phase 1 local testing (ticket 0024 §Phase 1).
"""

import argparse
import sys
import uuid

import httpx

DEFAULT_BASE_URL = "https://api.staging.carddroper.com"
SMOKE_PASSWORD = "SmokeTest12345"


def _fail(message: str) -> None:
    print(f"SMOKE FAIL: subscribe — {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the subscription state endpoint against staging.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    slug = uuid.uuid4().hex[:8]
    email = f"smoke+subscribe-{slug}@carddroper.com"

    with httpx.Client(timeout=10) as client:
        # ------------------------------------------------------------------
        # 1. Register
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/register",
            json={"email": email, "password": SMOKE_PASSWORD, "full_name": "Smoke Subscribe"},
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
        # 2. GET /billing/subscription — expect no-subscription envelope
        # ------------------------------------------------------------------
        resp = client.get(f"{base}/billing/subscription", headers=auth_headers)
        if resp.status_code != 200:
            _fail(f"GET /billing/subscription returned {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        has_subscription = body.get("has_subscription")
        tier_key = body.get("tier_key")
        status = body.get("status")

        if has_subscription is not False:
            _fail(f"Expected has_subscription=false, got {has_subscription!r}")

        if tier_key is not None:
            _fail(f"Expected tier_key=null, got {tier_key!r}")

        if status is not None:
            _fail(f"Expected status=null, got {status!r}")

        # ------------------------------------------------------------------
        # 3. Logout (cleanup)
        # ------------------------------------------------------------------
        resp = client.post(
            f"{base}/auth/logout",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code != 200:
            _fail(f"/auth/logout returned {resp.status_code}: {resp.text[:200]}")

    print("SMOKE OK: subscribe")


if __name__ == "__main__":
    main()
