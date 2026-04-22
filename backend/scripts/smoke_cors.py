#!/usr/bin/env python3
"""Smoke test: CORS preflight — verifies the backend allows browser requests from the frontend origin.

Run against local docker-compose:
    python backend/scripts/smoke_cors.py --base-url http://localhost:8000 --origin http://localhost:3000

Run against staging (after deploy):
    python backend/scripts/smoke_cors.py \\
        --base-url https://api.staging.carddroper.com \\
        --origin https://staging.carddroper.com
"""

import argparse
import sys

import httpx

DEFAULT_BASE_URL = "https://api.staging.carddroper.com"
DEFAULT_ORIGIN = "https://staging.carddroper.com"


def _fail(message: str) -> None:
    print(f"SMOKE FAIL: cors — {message}", file=sys.stderr)
    sys.exit(1)


def _assert(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        suffix = f": {detail}" if detail else ""
        print(f"  FAIL  {label}{suffix}", file=sys.stderr)
        _fail(label)


def _check_preflight(client: httpx.Client, base: str, path: str, method: str, origin: str) -> None:
    """Issue an OPTIONS preflight request and assert CORS headers are correct."""
    print(f"\n--- OPTIONS {path} (simulating {method} from {origin}) ---")
    resp = client.options(
        f"{base}{path}",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    _assert(
        f"status 200 or 204 (got {resp.status_code})",
        resp.status_code in (200, 204),
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    _assert(
        f"Access-Control-Allow-Origin == {origin!r} (got {acao!r})",
        acao == origin,
        "wildcard '*' is incompatible with credentials; exact origin required",
    )

    acac = resp.headers.get("access-control-allow-credentials", "")
    _assert(
        f"Access-Control-Allow-Credentials == 'true' (got {acac!r})",
        acac.lower() == "true",
    )

    acam = resp.headers.get("access-control-allow-methods", "")
    _assert(
        f"Access-Control-Allow-Methods includes {method!r} (got {acam!r})",
        method in acam,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test CORS preflight responses against the API.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the API (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--origin",
        default=DEFAULT_ORIGIN,
        help=f"Frontend origin to send in the Origin header (default: {DEFAULT_ORIGIN})",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    origin = args.origin.rstrip("/")

    print(f"Smoke: cors  base={base}  origin={origin}")

    with httpx.Client(timeout=10) as client:
        # /auth/login — POST endpoint; the primary preflight target (credentials flow)
        _check_preflight(client, base, "/auth/login", "POST", origin)

        # /auth/me — GET endpoint; confirm CORS is applied uniformly, not only to POST routes
        _check_preflight(client, base, "/auth/me", "GET", origin)

    print("\nSMOKE OK: cors")


if __name__ == "__main__":
    main()
