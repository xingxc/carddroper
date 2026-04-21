#!/usr/bin/env python3
"""Smoke test: send_email helper — exercises the real SendGrid API end-to-end.

This script REQUIRES a real SENDGRID_API_KEY and a valid template ID. It will
refuse to run in the no-key dev-fallback path so that a false-positive result
is impossible.

Default invocation (staging, with secrets from Secret Manager):
    SENDGRID_API_KEY=$(gcloud secrets versions access latest --secret=carddroper-sendgrid-api-key --project=carddroper-staging) \\
    SENDGRID_TEMPLATE_VERIFY_EMAIL=$(gcloud secrets versions access latest --secret=carddroper-sendgrid-template-verify-email --project=carddroper-staging) \\
    .venv/bin/python scripts/smoke_email.py

Manual overrides:
    .venv/bin/python scripts/smoke_email.py --template RESET_PASSWORD --to foo@carddroper.com
"""

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure app/ is importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.email_service import EmailTemplate, init_email_client, send_email  # noqa: E402


def _stub_data(template: EmailTemplate, to: str) -> dict:
    """Build minimal stub dynamic_template_data for the given template."""
    if template == EmailTemplate.VERIFY_EMAIL:
        return {
            "verify_url": f"{settings.FRONTEND_BASE_URL}/verify-email?token=smoke-test-token",
            "full_name": "Smoke Test",
        }
    if template == EmailTemplate.RESET_PASSWORD:
        return {
            "reset_url": f"{settings.FRONTEND_BASE_URL}/reset-password?token=smoke-test-token",
            "full_name": "Smoke Test",
        }
    if template == EmailTemplate.CHANGE_EMAIL:
        return {
            "change_url": (
                f"{settings.FRONTEND_BASE_URL}/confirm-email-change?token=smoke-test-token"
            ),
            "full_name": "Smoke Test",
            "new_email": to,
        }
    if template == EmailTemplate.EMAIL_CHANGED:
        return {
            "old_email": to,
            "new_email": "new@example.com",
            "change_date": datetime.now(timezone.utc).isoformat(),
            "support_email": "support@carddroper.com",
        }
    if template == EmailTemplate.CREDITS_PURCHASED:
        return {
            "credits": 100,
            "amount_paid": "$9.99",
        }
    return {}


async def _run(template_name: str, to: str) -> None:
    try:
        template = EmailTemplate[template_name.upper()]
    except KeyError:
        valid = ", ".join(t.name for t in EmailTemplate)
        print(
            f"SMOKE FAIL: email — unknown template {template_name!r}. Valid values: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    init_email_client()
    data = _stub_data(template, to)
    message_id = await send_email(template=template, to=to, dynamic_template_data=data)
    print(f"sg_message_id={message_id}")


def main() -> None:
    slug = uuid.uuid4().hex[:8]
    # Uses the real domain, not a .test / .invalid / .example TLD, because email-validator
    # rejects special-use TLDs. The smoke+ prefix lets a nightly sweep reap these.
    default_to = f"smoke+email-{slug}@carddroper.com"

    parser = argparse.ArgumentParser(
        description="Smoke-test send_email against the real SendGrid API.",
    )
    parser.add_argument(
        "--to",
        default=default_to,
        help="Recipient email address (default: smoke+email-<uuid8>@carddroper.com)",
    )
    parser.add_argument(
        "--template",
        default="VERIFY_EMAIL",
        help="EmailTemplate name, e.g. VERIFY_EMAIL (default: VERIFY_EMAIL)",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Pre-flight: refuse to run without real credentials.
    # The no-key fallback in send_email is correct for local dev; it is NOT
    # a valid smoke result — a fallback run proves nothing about SendGrid wiring.
    # ------------------------------------------------------------------
    api_key = settings.SENDGRID_API_KEY.get_secret_value()
    if not api_key:
        print(
            "SMOKE FAIL: email — SENDGRID_API_KEY is empty. "
            "This smoke must exercise the real SendGrid API. Re-run with:\n"
            "  SENDGRID_API_KEY=$(gcloud secrets versions access latest "
            "--secret=carddroper-sendgrid-api-key --project=carddroper-staging) \\\n"
            "  SENDGRID_TEMPLATE_VERIFY_EMAIL=$(gcloud secrets versions access latest "
            "--secret=carddroper-sendgrid-template-verify-email --project=carddroper-staging) \\\n"
            "  .venv/bin/python scripts/smoke_email.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve the template name early so we can check the corresponding setting.
    template_name = args.template.upper()
    if template_name not in EmailTemplate.__members__:
        valid = ", ".join(t.name for t in EmailTemplate)
        print(
            f"SMOKE FAIL: email — unknown template {args.template!r}. Valid values: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    template_setting_name = f"SENDGRID_TEMPLATE_{template_name}"
    template_id = getattr(settings, template_setting_name, None) or ""
    if not template_id:
        print(
            f"SMOKE FAIL: email — {template_setting_name} is empty. "
            "This smoke must exercise the real SendGrid API. Re-run with:\n"
            "  SENDGRID_API_KEY=$(gcloud secrets versions access latest "
            "--secret=carddroper-sendgrid-api-key --project=carddroper-staging) \\\n"
            f"  {template_setting_name}=$(gcloud secrets versions access latest "
            f"--secret=carddroper-sendgrid-template-{template_name.lower().replace('_', '-')} "
            "--project=carddroper-staging) \\\n"
            "  .venv/bin/python scripts/smoke_email.py",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(_run(args.template, args.to))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"SMOKE FAIL: email — {exc}", file=sys.stderr)
        sys.exit(1)

    print("SMOKE OK: email")


if __name__ == "__main__":
    main()
