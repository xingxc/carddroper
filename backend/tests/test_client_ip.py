"""Unit tests for the get_client_ip helper in app.routes.auth.

These tests are synchronous and construct Starlette Request objects directly
from crafted ASGI scopes — no DB or running app required.
"""

from starlette.requests import Request

from app.routes.auth import get_client_ip


def _make_request(xff: str | None = None, client_host: str | None = "127.0.0.1") -> Request:
    """Build a minimal Starlette Request from a crafted ASGI scope."""
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": headers,
        "client": (client_host, 12345) if client_host is not None else None,
    }
    return Request(scope)


def test_get_client_ip_uses_forwarded_for_when_present():
    """When X-Forwarded-For is set, the helper returns that IP."""
    request = _make_request(xff="1.2.3.4")
    assert get_client_ip(request) == "1.2.3.4"


def test_get_client_ip_uses_first_entry_when_multiple():
    """When X-Forwarded-For has multiple comma-separated entries, the first is returned."""
    request = _make_request(xff="1.2.3.4, 10.0.0.1, 10.0.0.2")
    assert get_client_ip(request) == "1.2.3.4"


def test_get_client_ip_falls_back_to_client_host():
    """Without X-Forwarded-For, the helper falls back to request.client.host."""
    request = _make_request(xff=None, client_host="127.0.0.1")
    assert get_client_ip(request) == "127.0.0.1"


def test_get_client_ip_returns_unknown_when_no_client():
    """Without X-Forwarded-For and with request.client=None, the helper returns 'unknown'."""
    request = _make_request(xff=None, client_host=None)
    assert get_client_ip(request) == "unknown"
