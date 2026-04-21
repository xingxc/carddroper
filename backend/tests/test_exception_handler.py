"""Tests for the global 500 exception handler (ticket 0011, Deliverable A).

Strategy: we test `internal_error_handler` directly (unit test) and also confirm
it is wired into the main app. A direct integration test through the full
middleware stack is complicated by Starlette's BaseHTTPMiddleware behaviour
(ExceptionGroup wrapping), so the authoritative check is:
  1. Call the handler directly and assert shape + logging.
  2. Confirm it is registered in app.exception_handlers under Exception.
  3. Smoke the route via raise_app_exceptions=False to confirm a 500 is returned.
"""

import logging

import httpx
import pytest
import pytest_asyncio
from fastapi import Request
from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Unit test: call the handler directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_returns_correct_json_shape(caplog):
    """internal_error_handler returns 500 JSON with INTERNAL_ERROR code."""
    import json

    from app.main import internal_error_handler

    # Build a minimal fake Request (no actual ASGI app needed for attribute access).
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test/boom",
        "query_string": b"",
        "headers": [],
    }

    req = Request(scope)
    # request.state auto-initialises; request_id will be absent → getattr returns None.

    exc = ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="app.main"):
        response: JSONResponse = await internal_error_handler(req, exc)

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "An unexpected error occurred."
    assert "request_id" in body["error"]


@pytest.mark.asyncio
async def test_handler_redacts_exception_details(caplog):
    """Response body must NOT contain exception class or message text."""
    from app.main import internal_error_handler

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test/boom",
        "query_string": b"",
        "headers": [],
    }
    req = Request(scope)
    exc = ValueError("boom")

    with caplog.at_level(logging.ERROR):
        response: JSONResponse = await internal_error_handler(req, exc)

    body_text = response.body.decode()
    assert "boom" not in body_text
    assert "ValueError" not in body_text
    assert "Traceback" not in body_text


@pytest.mark.asyncio
async def test_handler_logs_unhandled_exception_event(caplog):
    """Handler must log at ERROR level with event=unhandled_exception."""
    from app.main import internal_error_handler

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test/boom",
        "query_string": b"",
        "headers": [],
    }
    req = Request(scope)
    exc = ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="app.main"):
        await internal_error_handler(req, exc)

    found = any(
        (
            "unhandled_exception" in rec.getMessage()
            or getattr(rec, "exc_type", None) == "ValueError"
        )
        and rec.levelno >= logging.ERROR
        for rec in caplog.records
    )
    assert found, (
        "Expected ERROR log with 'unhandled_exception' from app.main. "
        f"Got: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Wiring check: handler is registered in main app
# ---------------------------------------------------------------------------


def test_exception_handler_registered_in_app():
    """Exception (catch-all) must be registered as a handler on the main app."""
    from app.main import app as fastapi_app
    from app.main import internal_error_handler

    # FastAPI stores exception handlers in app.exception_handlers dict keyed by type.
    # The Exception entry must map to our internal_error_handler.
    handlers = fastapi_app.exception_handlers
    assert Exception in handlers, (
        f"Exception not registered as exception handler. Registered: {list(handlers.keys())}"
    )
    assert handlers[Exception] is internal_error_handler


# ---------------------------------------------------------------------------
# Integration smoke: 500 is returned (body shape verified above via unit test)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def boom_client():
    """Client with a temporary /test/boom route and raise_app_exceptions=False."""
    from app.main import app as fastapi_app

    async def _boom():
        raise ValueError("boom")

    fastapi_app.add_api_route("/test/boom-smoke", _boom, methods=["GET"])

    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    fastapi_app.routes[:] = [
        r for r in fastapi_app.routes if getattr(r, "path", None) != "/test/boom-smoke"
    ]


@pytest.mark.asyncio
async def test_unhandled_exception_smoke_returns_500(boom_client):
    """Integration: hitting a route that raises returns HTTP 500."""
    r = await boom_client.get("/test/boom-smoke")
    assert r.status_code == 500
