"""Smoke-test CLI for send_email.

Usage (from backend/):
    .venv/bin/python scripts/smoke_email.py --to=foo@bar.com --template=VERIFY_EMAIL

Reads config from environment (same Settings as the app).
Prints the returned sg_message_id (or "local-<uuid>") and exits 0 on success.

Dev dry run (no key needed):
    SENDGRID_API_KEY= .venv/bin/python scripts/smoke_email.py \
        --to=you@example.com --template=VERIFY_EMAIL
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure app/ is importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.email_service import EmailTemplate, init_email_client, send_email  # noqa: E402


def _stub_data(template: EmailTemplate, to: str) -> dict:
    """Build a minimal stub dynamic_template_data for the given template."""
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
            "change_url": f"{settings.FRONTEND_BASE_URL}/confirm-email-change?token=smoke-test-token",
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


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test send_email against the real API or dev fallback."
    )
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--template", required=True, help="EmailTemplate name, e.g. VERIFY_EMAIL")
    args = parser.parse_args()

    try:
        template = EmailTemplate[args.template.upper()]
    except KeyError:
        valid = ", ".join(t.name for t in EmailTemplate)
        print(f"Unknown template '{args.template}'. Valid values: {valid}", file=sys.stderr)
        sys.exit(1)

    init_email_client()
    data = _stub_data(template, args.to)
    message_id = await send_email(template=template, to=args.to, dynamic_template_data=data)
    print(f"sg_message_id={message_id}")


if __name__ == "__main__":
    asyncio.run(main())
