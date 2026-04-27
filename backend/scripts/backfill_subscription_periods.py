"""One-shot: backfill subscriptions.current_period_start/end from Stripe.

Fetches each subscription row that has NULL period fields, retrieves the
current state from Stripe, and updates the row. Safe to re-run — rows with
already-populated periods are skipped.

Usage (inside container):
    docker-compose exec backend .venv/bin/python scripts/backfill_subscription_periods.py

Usage (local venv, requires DATABASE_URL + STRIPE_SECRET_KEY in environment):
    .venv/bin/python scripts/backfill_subscription_periods.py

Exit codes:
    0  — completed (all NULL-period rows resolved or no NULL rows found).
    1  — one or more rows failed to backfill (partial success; safe to retry).
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.subscription import Subscription

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _to_naive_utc(unix_ts: int | float | None) -> datetime | None:
    """Convert Stripe Unix timestamp to naive UTC datetime (chassis convention)."""
    if unix_ts is None:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).replace(tzinfo=None)


async def main() -> int:
    """Backfill NULL period fields on subscriptions rows from Stripe API.

    Returns 0 on full success, 1 if any row failed (so the caller can retry).
    """
    # Import stripe after settings are loaded so API key can be set.
    try:
        import stripe  # type: ignore[import]
    except ImportError:
        logger.error("stripe package not available. Is the venv activated?")
        return 1

    stripe.api_key = settings.STRIPE_SECRET_KEY.get_secret_value()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Subscription).where(
                or_(
                    Subscription.current_period_start.is_(None),
                    Subscription.current_period_end.is_(None),
                )
            )
        )
        rows = result.scalars().all()

    if not rows:
        logger.info("No subscriptions with NULL period fields found. Nothing to backfill.")
        return 0

    logger.info("Found %d subscription(s) with NULL period field(s).", len(rows))

    failed = 0
    for sub_row in rows:
        stripe_sub_id = sub_row.stripe_subscription_id
        try:
            stripe_sub = await asyncio.to_thread(stripe.Subscription.retrieve, stripe_sub_id)

            # Defensive dual-access: attribute first, then dict-style.
            raw_start = getattr(stripe_sub, "current_period_start", None) or (
                stripe_sub.get("current_period_start") if hasattr(stripe_sub, "get") else None
            )
            raw_end = getattr(stripe_sub, "current_period_end", None) or (
                stripe_sub.get("current_period_end") if hasattr(stripe_sub, "get") else None
            )

            if raw_start is None and raw_end is None:
                logger.warning(
                    "Stripe returned no period data for %s — skipping.",
                    stripe_sub_id,
                )
                continue

            async with AsyncSessionLocal() as db:
                async with db.begin():
                    result = await db.execute(
                        select(Subscription).where(
                            Subscription.stripe_subscription_id == stripe_sub_id
                        )
                    )
                    row = result.scalar_one_or_none()
                    if row is None:
                        logger.warning(
                            "Row for %s disappeared between query and update — skipping.",
                            stripe_sub_id,
                        )
                        continue

                    updated = False
                    if row.current_period_start is None and raw_start is not None:
                        row.current_period_start = _to_naive_utc(raw_start)
                        updated = True
                    if row.current_period_end is None and raw_end is not None:
                        row.current_period_end = _to_naive_utc(raw_end)
                        updated = True

                    if updated:
                        db.add(row)
                        logger.info(
                            "Backfilled sub_id=%s (user_id=%s): start=%s end=%s",
                            stripe_sub_id,
                            row.user_id,
                            row.current_period_start,
                            row.current_period_end,
                        )
                    else:
                        logger.info(
                            "sub_id=%s already had period data — no update needed.",
                            stripe_sub_id,
                        )

        except Exception as exc:
            logger.error("FAILED to backfill %s: %s", stripe_sub_id, exc)
            failed += 1

    if failed:
        logger.error("%d subscription(s) failed to backfill. Re-run to retry.", failed)
        return 1

    logger.info("Backfill complete. All NULL-period subscriptions updated.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
