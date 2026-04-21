"""Production-grade SendGrid email helper.

Public API:
    class EmailTemplate(str, Enum)
    async def send_email(*, template, to, dynamic_template_data, from_address, from_name) -> str
    def init_email_client() -> None
    def close_email_client() -> None
"""

import asyncio
import hashlib
from enum import Enum
from uuid import uuid4

import requests
import sendgrid
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, MailSettings, SandBoxMode
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Template enum
# ---------------------------------------------------------------------------


class EmailTemplate(str, Enum):
    VERIFY_EMAIL = "VERIFY_EMAIL"
    RESET_PASSWORD = "RESET_PASSWORD"
    CHANGE_EMAIL = "CHANGE_EMAIL"
    EMAIL_CHANGED = "EMAIL_CHANGED"
    CREDITS_PURCHASED = "CREDITS_PURCHASED"


# ---------------------------------------------------------------------------
# Template → Settings field mapping
# ---------------------------------------------------------------------------

_TEMPLATE_FIELD: dict[EmailTemplate, str] = {
    EmailTemplate.VERIFY_EMAIL: "SENDGRID_TEMPLATE_VERIFY_EMAIL",
    EmailTemplate.RESET_PASSWORD: "SENDGRID_TEMPLATE_RESET_PASSWORD",
    EmailTemplate.CHANGE_EMAIL: "SENDGRID_TEMPLATE_CHANGE_EMAIL",
    EmailTemplate.EMAIL_CHANGED: "SENDGRID_TEMPLATE_EMAIL_CHANGED",
    EmailTemplate.CREDITS_PURCHASED: "SENDGRID_TEMPLATE_CREDITS_PURCHASED",
}

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_client: SendGridAPIClient | None = None


def init_email_client() -> None:
    """Initialise the singleton SendGrid client. Call during app startup."""
    global _client
    api_key = settings.SENDGRID_API_KEY.get_secret_value()
    if api_key:
        _client = SendGridAPIClient(api_key)
        # 5-second per-attempt HTTP timeout
        _client.client.session.timeout = 5.0
        logger.info("SendGrid client initialised")
    else:
        logger.info("SENDGRID_API_KEY not set — email sending will use dev fallback")


def close_email_client() -> None:
    """Tear down the singleton client. No-op for SendGrid SDK; here for symmetry."""
    global _client
    _client = None


# ---------------------------------------------------------------------------
# Retry predicate
# ---------------------------------------------------------------------------


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    return status in {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------


async def send_email(
    *,
    template: EmailTemplate,
    to: str,
    dynamic_template_data: dict,
    from_address: str | None = None,
    from_name: str | None = None,
) -> str:
    """Send an email via SendGrid Dynamic Templates.

    Returns the SendGrid x-message-id on success, or "local-<uuid4>" when
    SENDGRID_API_KEY is empty (dev fallback).

    Raises ValueError if the template ID is not configured.
    Raises on final tenacity failure after 3 attempts.
    """
    to_hash = hashlib.sha256(to.lower().encode()).hexdigest()

    # No-key dev fallback — MUST use .get_secret_value() because SecretStr("") is truthy.
    if not settings.SENDGRID_API_KEY.get_secret_value():
        mock_id = f"local-{uuid4()}"
        # Reconstruct a single safe preview URL from whichever key is present.
        preview_url: str | None = (
            dynamic_template_data.get("verify_url")
            or dynamic_template_data.get("reset_url")
            or dynamic_template_data.get("change_url")
        )
        logger.info(
            "email_skipped_no_key",
            extra={
                "event": "email_skipped_no_key",
                "template": template.name,
                "to_hash": to_hash,
                "mock_message_id": mock_id,
                "dev_preview_url": preview_url,
            },
        )
        return mock_id

    # Resolve template ID — fail loud on missing config.
    field_name = _TEMPLATE_FIELD[template]
    template_id: str = getattr(settings, field_name, "")
    if not template_id:
        raise ValueError(f"{field_name} is not configured")

    sender_email = from_address or settings.FROM_EMAIL
    sender_name = from_name or settings.FROM_NAME

    # Build the Mail object once; reuse across retry attempts.
    mail = Mail()
    mail.from_email = sendgrid.helpers.mail.Email(sender_email, sender_name)
    mail.to = sendgrid.helpers.mail.To(to)
    mail.template_id = template_id
    mail.dynamic_template_data = dynamic_template_data

    if settings.SENDGRID_SANDBOX:
        mail_settings = MailSettings()
        sandbox = SandBoxMode()
        sandbox.enable = True
        mail_settings.sandbox_mode = sandbox
        mail.mail_settings = mail_settings

    # Use module-level client if available; otherwise create per-call (should not happen
    # in normal operation since init_email_client() was called at startup).
    client = _client
    if client is None:
        client = SendGridAPIClient(settings.SENDGRID_API_KEY.get_secret_value())
        client.client.session.timeout = 5.0

    attempt_count = 0

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception(_should_retry),
        reraise=True,
    )
    async def _send_with_retry() -> str:
        nonlocal attempt_count
        attempt_count += 1
        try:
            response = await asyncio.to_thread(client.send, mail)
            message_id: str = response.headers.get("x-message-id") or str(uuid4())
            logger.info(
                "email_sent",
                extra={
                    "event": "email_sent",
                    "template": template.name,
                    "to_hash": to_hash,
                    "sg_message_id": message_id,
                    "attempt": attempt_count,
                },
            )
            return message_id
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            logger.error(
                "email_send_failed",
                extra={
                    "event": "email_send_failed",
                    "template": template.name,
                    "to_hash": to_hash,
                    "status_code": status,
                    "attempt": attempt_count,
                    "error": type(exc).__name__,
                },
            )
            raise

    return await _send_with_retry()
