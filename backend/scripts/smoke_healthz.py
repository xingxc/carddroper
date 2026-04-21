#!/usr/bin/env python3
"""Smoke test: GET /health — assert 200 + expected body shape."""

import argparse
import sys

import httpx

DEFAULT_BASE_URL = "https://api.staging.carddroper.com"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the /health endpoint.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    url = args.base_url.rstrip("/") + "/health"

    try:
        response = httpx.get(url, timeout=10)
    except httpx.RequestError as exc:
        print(f"SMOKE FAIL: healthz — request error: {exc}", file=sys.stderr)
        sys.exit(1)

    if response.status_code != 200:
        print(
            f"SMOKE FAIL: healthz — expected 200, got {response.status_code}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        body = response.json()
    except Exception as exc:
        print(f"SMOKE FAIL: healthz — could not parse JSON body: {exc}", file=sys.stderr)
        sys.exit(1)

    if body.get("status") != "ok":
        print(
            f"SMOKE FAIL: healthz — expected status='ok', got {body.get('status')!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    if "database" in body and body["database"] != "connected":
        print(
            f"SMOKE FAIL: healthz — expected database='connected', got {body['database']!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("SMOKE OK: healthz")


if __name__ == "__main__":
    main()
