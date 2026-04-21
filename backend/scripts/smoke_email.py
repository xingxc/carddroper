#!/usr/bin/env python3
"""Smoke test: send_email helper — exercises the SendGrid send path (or dev fallback).

Default invocation (no arguments needed):
    .venv/bin/python scripts/smoke_email.py

Manual overrides:
    .venv/bin/python scripts/smoke_email.py --template RESET_PASSWORD --to foo@example.com

When SENDGRID_API_KEY is empty the service logs email_skipped_no_key and returns a
"local-<uuid>" mock id — that still counts as SMOKE OK (dev / CI case).
A staging run with the key set exercises the real SendGrid path.
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
    default_to = f"smoke+email-{slug}@carddroper.test"

    parser = argparse.ArgumentParser(
        description="Smoke-test send_email against the real SendGrid API or dev fallback.",
    )
    parser.add_argument(
        "--to",
        default=default_to,
        help="Recipient email address (default: smoke+email-<uuid8>@carddroper.test)",
    )
    parser.add_argument(
        "--template",
        default="VERIFY_EMAIL",
        help="EmailTemplate name, e.g. VERIFY_EMAIL (default: VERIFY_EMAIL)",
    )
    args = parser.parse_args()

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
