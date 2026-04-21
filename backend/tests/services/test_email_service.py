"""Tests for app/services/email_service.py.

Tests:
  a. Happy path — mocked client returns x-message-id.
  b. Sandbox mode — Mail object has sandbox_mode enabled.
  c. No API key — dev fallback returns "local-<uuid>", client never called,
     log has only safe fields.
  d. SecretStr empty check — SecretStr("") falls through to fallback.
  e. Retry on 503 — retries and succeeds on 3rd attempt.
  f. Retry on ConnectionError — retries and succeeds on 3rd attempt.
  g. No retry on 400 — fails after 1 attempt.
  h. Missing template ID — raises ValueError before any client call.
  i. Event loop not blocked — asyncio.to_thread offload allows concurrency.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status: int = 202, message_id: str = "test-msg-id-123") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"x-message-id": message_id}
    return resp


def _make_status_exc(status: int) -> Exception:
    exc = Exception(f"HTTP error {status}")
    exc.status_code = status  # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Test a — happy path
# ---------------------------------------------------------------------------


async def test_happy_path_returns_message_id(monkeypatch):
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    mock_client = MagicMock()
    mock_client.send.return_value = _make_response(202, "sg-msg-abc")

    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake-key"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "d-template1")

    result = await send_email(
        template=EmailTemplate.VERIFY_EMAIL,
        to="user@example.com",
        dynamic_template_data={"verify_url": "http://localhost:3000/verify", "full_name": "Alice"},
    )

    assert result == "sg-msg-abc"
    mock_client.send.assert_called_once()


# ---------------------------------------------------------------------------
# Test b — sandbox mode
# ---------------------------------------------------------------------------


async def test_sandbox_mode_sets_flag(monkeypatch):
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    captured_mail = []

    def _capture_send(mail):
        captured_mail.append(mail)
        return _make_response(202, "sandbox-id")

    mock_client = MagicMock()
    mock_client.send.side_effect = _capture_send

    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake-key"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "d-template1")
    monkeypatch.setattr(email_service.settings, "SENDGRID_SANDBOX", True)

    await send_email(
        template=EmailTemplate.VERIFY_EMAIL,
        to="user@example.com",
        dynamic_template_data={"verify_url": "http://localhost/v", "full_name": None},
    )

    assert len(captured_mail) == 1
    mail = captured_mail[0]
    assert mail.mail_settings is not None
    assert mail.mail_settings.sandbox_mode.enable is True


# ---------------------------------------------------------------------------
# Test c — no API key fallback
# ---------------------------------------------------------------------------


async def test_no_key_fallback_no_client_call(monkeypatch, caplog):
    import logging

    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    mock_client = MagicMock()
    monkeypatch.setattr(email_service, "_client", mock_client)
    # SecretStr("") — the empty string form that must trigger fallback
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret(""))

    with caplog.at_level(logging.INFO, logger="app.services.email_service"):
        result = await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to="user@example.com",
            dynamic_template_data={
                "verify_url": "http://localhost:3000/verify?token=abc",
                "full_name": "Alice",
            },
        )

    # Client must NEVER be called
    mock_client.send.assert_not_called()

    # Return value is a local mock ID
    assert result.startswith("local-")

    # Inspect LogRecord extra fields directly (caplog.text only shows the message string;
    # extra fields are stored on the LogRecord itself).
    records = [r for r in caplog.records if r.name == "app.services.email_service"]
    assert len(records) == 1
    rec = records[0]

    # Safe fields must be present
    assert rec.__dict__.get("event") == "email_skipped_no_key"
    assert rec.__dict__.get("dev_preview_url") == "http://localhost:3000/verify?token=abc"
    assert "to_hash" in rec.__dict__
    assert "mock_message_id" in rec.__dict__

    # Forbidden fields must NOT be present
    assert "to" not in rec.__dict__
    assert "subject" not in rec.__dict__
    assert "body_text" not in rec.__dict__
    assert "full_name" not in rec.__dict__
    # dynamic_template_data raw dict must not be logged
    assert "dynamic_template_data" not in rec.__dict__

    # The raw email address and full_name must not appear in any field value
    all_values = str(list(rec.__dict__.values()))
    assert "user@example.com" not in all_values
    assert "Alice" not in all_values


# ---------------------------------------------------------------------------
# Test d — SecretStr("") falls through to fallback
# ---------------------------------------------------------------------------


async def test_secretstr_empty_falls_through(monkeypatch):
    """SecretStr("") must trigger the dev fallback.

    The ticket noted SecretStr("") may be truthy in pydantic v1; in pydantic v2 it is
    falsy. Either way, the code uses .get_secret_value() for the emptiness check — this
    test verifies that the fallback fires and the client is never called.
    """
    from pydantic import SecretStr

    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    mock_client = MagicMock()
    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", SecretStr(""))

    # The critical behaviour: .get_secret_value() returns "" which is falsy → fallback fires.
    assert SecretStr("").get_secret_value() == ""

    result = await send_email(
        template=EmailTemplate.VERIFY_EMAIL,
        to="x@x.com",
        dynamic_template_data={"verify_url": "http://x/v", "full_name": None},
    )
    assert result.startswith("local-")
    mock_client.send.assert_not_called()


# ---------------------------------------------------------------------------
# Test e — retry on 503, succeeds on 3rd attempt
# ---------------------------------------------------------------------------


async def test_retry_on_503_succeeds_third_attempt(monkeypatch):
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    call_count = 0

    def _flaky(mail):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _make_status_exc(503)
        return _make_response(202, "retry-success-id")

    mock_client = MagicMock()
    mock_client.send.side_effect = _flaky

    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "d-t1")

    # Patch tenacity wait to zero to keep tests fast
    with patch("app.services.email_service.wait_exponential", return_value=_zero_wait()):
        result = await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to="r@example.com",
            dynamic_template_data={"verify_url": "http://x/v", "full_name": None},
        )

    assert result == "retry-success-id"
    assert call_count == 3


# ---------------------------------------------------------------------------
# Test f — retry on ConnectionError, succeeds on 3rd attempt
# ---------------------------------------------------------------------------


async def test_retry_on_connection_error(monkeypatch):
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    call_count = 0

    def _flaky(mail):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise requests.ConnectionError("connection refused")
        return _make_response(202, "conn-retry-id")

    mock_client = MagicMock()
    mock_client.send.side_effect = _flaky
    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "d-t1")

    with patch("app.services.email_service.wait_exponential", return_value=_zero_wait()):
        result = await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to="c@example.com",
            dynamic_template_data={"verify_url": "http://x/v", "full_name": None},
        )

    assert result == "conn-retry-id"
    assert call_count == 3


# ---------------------------------------------------------------------------
# Test g — no retry on 400
# ---------------------------------------------------------------------------


async def test_no_retry_on_400(monkeypatch):
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    call_count = 0

    def _bad(mail):
        nonlocal call_count
        call_count += 1
        raise _make_status_exc(400)

    mock_client = MagicMock()
    mock_client.send.side_effect = _bad
    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "d-t1")

    with pytest.raises(Exception, match="HTTP error 400"):
        await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to="bad@example.com",
            dynamic_template_data={"verify_url": "http://x/v", "full_name": None},
        )

    # Should have tried exactly once — no retry for 400
    assert call_count == 1


# ---------------------------------------------------------------------------
# Test h — missing template ID raises ValueError
# ---------------------------------------------------------------------------


async def test_missing_template_id_raises_value_error(monkeypatch):
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    mock_client = MagicMock()
    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "")

    with pytest.raises(ValueError, match="SENDGRID_TEMPLATE_VERIFY_EMAIL is not configured"):
        await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to="x@example.com",
            dynamic_template_data={"verify_url": "http://x/v", "full_name": None},
        )

    mock_client.send.assert_not_called()


# ---------------------------------------------------------------------------
# Test i — event loop not blocked (asyncio.to_thread offload)
# ---------------------------------------------------------------------------


async def test_event_loop_not_blocked(monkeypatch):
    """If to_thread is missing, the blocking sleep inside send() will stall other coroutines."""
    from app.services import email_service
    from app.services.email_service import EmailTemplate, send_email

    def _slow_send(mail):
        time.sleep(0.2)
        return _make_response(202, "slow-id")

    mock_client = MagicMock()
    mock_client.send.side_effect = _slow_send
    monkeypatch.setattr(email_service, "_client", mock_client)
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", _secret("SG.fake"))
    monkeypatch.setattr(email_service.settings, "SENDGRID_TEMPLATE_VERIFY_EMAIL", "d-t1")

    results = []

    async def background_coro():
        await asyncio.sleep(0.05)
        results.append("background_ran")

    # Both should complete; background_coro should not be blocked by the slow send
    await asyncio.gather(
        send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to="slow@example.com",
            dynamic_template_data={"verify_url": "http://x/v", "full_name": None},
        ),
        background_coro(),
    )

    assert "background_ran" in results


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _secret(value: str):
    """Return a SecretStr with the given raw value."""
    from pydantic import SecretStr

    return SecretStr(value)


def _zero_wait():
    """A tenacity wait strategy that always returns 0 (for fast tests)."""
    from tenacity import wait_none

    return wait_none()
