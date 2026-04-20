from __future__ import annotations

import html as html_module
from typing import Optional

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Content, Email, Mail, To

from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)


def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    """Send via SendGrid. Without API key, log to stdout and return True (dev mode)."""
    if not settings.SENDGRID_API_KEY:
        logger.info(
            "Email (dev — not sent, SENDGRID_API_KEY unset)",
            extra={"to": to, "subject": subject, "body_text": text or html[:500]},
        )
        return True

    message = Mail(
        from_email=Email(settings.FROM_EMAIL, settings.FROM_NAME),
        to_emails=To(to),
        subject=subject,
        html_content=Content("text/html", html),
    )
    if text:
        message.add_content(Content("text/plain", text))

    try:
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        logger.info(
            "Email sent",
            extra={"to": to, "subject": subject, "status_code": response.status_code},
        )
        return True
    except Exception as e:
        logger.error(
            "SendGrid email failed",
            extra={"to": to, "subject": subject, "error": str(e)},
        )
        return False


def _button(url: str, label: str) -> str:
    return (
        f'<p style="text-align:center;margin:30px 0;">'
        f'<a href="{url}" style="background:#2563eb;color:#fff;padding:12px 24px;'
        f'text-decoration:none;border-radius:6px;font-weight:bold;">{label}</a></p>'
    )


def send_verification_email(email: str, token: str, full_name: Optional[str] = None) -> bool:
    safe_name = html_module.escape(full_name) if full_name else "there"
    url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    subject = "Verify your email — Carddroper"
    html = (
        f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">'
        f"<h2>Confirm your email</h2>"
        f"<p>Hi {safe_name},</p>"
        f"<p>Please verify your email to start using Carddroper. "
        f"This link expires in {settings.EMAIL_VERIFY_EXPIRY_HOURS} hours.</p>"
        f'{_button(url, "Verify email")}'
        f'<p style="color:#6b7280;font-size:14px;">If you didn\'t create an account, ignore this email.</p>'
        f"</div>"
    )
    text = (
        f"Hi {full_name or ''},\n\n"
        f"Verify your email: {url}\n\n"
        f"Link expires in {settings.EMAIL_VERIFY_EXPIRY_HOURS} hours."
    )
    return send_email(email, subject, html, text)


def send_password_reset(email: str, token: str, full_name: Optional[str] = None) -> bool:
    safe_name = html_module.escape(full_name) if full_name else "there"
    url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    subject = "Reset your password — Carddroper"
    html = (
        f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">'
        f"<h2>Reset your password</h2>"
        f"<p>Hi {safe_name},</p>"
        f"<p>We received a request to reset your password. "
        f"This link expires in {settings.PASSWORD_RESET_EXPIRY_MINUTES} minutes.</p>"
        f'{_button(url, "Reset password")}'
        f'<p style="color:#6b7280;font-size:14px;">If you didn\'t request this, you can safely ignore this email.</p>'
        f"</div>"
    )
    text = (
        f"Hi {full_name or ''},\n\n"
        f"Reset your password: {url}\n\n"
        f"Link expires in {settings.PASSWORD_RESET_EXPIRY_MINUTES} minutes."
    )
    return send_email(email, subject, html, text)


def send_email_change_verification(new_email: str, token: str, full_name: Optional[str] = None) -> bool:
    safe_name = html_module.escape(full_name) if full_name else "there"
    url = f"{settings.FRONTEND_URL}/confirm-email-change?token={token}"
    subject = "Confirm your new email — Carddroper"
    html = (
        f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">'
        f"<h2>Confirm your new email</h2>"
        f"<p>Hi {safe_name},</p>"
        f"<p>Click below to confirm this address as the new login email for your Carddroper account. "
        f"The link expires in {settings.EMAIL_CHANGE_EXPIRY_HOURS} hour(s).</p>"
        f'{_button(url, "Confirm new email")}'
        f'<p style="color:#6b7280;font-size:14px;">If you didn\'t request this change, you can ignore this email.</p>'
        f"</div>"
    )
    text = (
        f"Confirm your new Carddroper email: {url}\n\n"
        f"Link expires in {settings.EMAIL_CHANGE_EXPIRY_HOURS} hour(s)."
    )
    return send_email(new_email, subject, html, text)


def send_email_change_notification(old_email: str, new_email: str) -> bool:
    """Canary: notifies the OLD address that the email was changed."""
    safe_new = html_module.escape(new_email)
    subject = "Your Carddroper email was changed"
    html = (
        f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">'
        f"<h2>Email changed</h2>"
        f"<p>Your Carddroper login email was changed to <strong>{safe_new}</strong>.</p>"
        f"<p>If this wasn't you, contact support at "
        f'<a href="mailto:support@carddroper.com">support@carddroper.com</a> immediately.</p>'
        f"</div>"
    )
    text = (
        f"Your Carddroper email was changed to {new_email}.\n\n"
        f"If this wasn't you, contact support@carddroper.com immediately."
    )
    return send_email(old_email, subject, html, text)
