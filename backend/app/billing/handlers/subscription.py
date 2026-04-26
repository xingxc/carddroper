"""Handlers for subscription lifecycle + invoice webhook events.

Registered via the dispatch registry in app.billing.handlers.
The side-effect import that causes this module to load lives in
routes/billing.py, not in handlers/__init__.py.

Handles:
- customer.subscription.created  — upsert subscriptions row + grant subscription_grant
- customer.subscription.updated  — sync state (status, period, cancel flag, tier metadata)
- customer.subscription.deleted  — mark status='cancelled'; do NOT revoke balance
- invoice.paid                   — subscription_cycle → grant subscription_reset; subscription_create → no-op
- invoice.payment_failed         — mark status='past_due'
"""

import logging
from datetime import datetime, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.handlers import register
from app.billing.primitives import grant
from app.billing.reason import Reason
from app.config import settings
from app.models.subscription import Subscription

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_user_id(obj, event_id: str) -> int | None:
    """Extract and validate metadata.user_id from a Stripe object.

    Returns the integer user_id or None if missing/invalid (handler should log + return).
    """
    metadata = getattr(obj, "metadata", None) or {}
    raw = metadata.get("user_id") if hasattr(metadata, "get") else None
    if not raw:
        logger.warning(
            "subscription handler: missing metadata.user_id",
            extra={"event_id": event_id},
        )
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning(
            "subscription handler: invalid metadata.user_id=%r",
            raw,
            extra={"event_id": event_id},
        )
        return None


def _extract_price_metadata(price_obj, event_id: str) -> tuple[int, str] | None:
    """Extract grant_micros and tier_name from a Stripe Price object's metadata.

    Returns (grant_micros, tier_name) or None if any required key is missing.
    """
    metadata = getattr(price_obj, "metadata", None) or {}
    raw_grant = metadata.get("grant_micros") if hasattr(metadata, "get") else None
    tier_name = metadata.get("tier_name") if hasattr(metadata, "get") else None

    if not raw_grant:
        logger.warning(
            "subscription handler: Price missing metadata.grant_micros",
            extra={"event_id": event_id},
        )
        return None
    if not tier_name:
        logger.warning(
            "subscription handler: Price missing metadata.tier_name",
            extra={"event_id": event_id},
        )
        return None

    try:
        grant_micros = int(raw_grant)
    except (ValueError, TypeError):
        logger.warning(
            "subscription handler: invalid metadata.grant_micros=%r",
            raw_grant,
            extra={"event_id": event_id},
        )
        return None

    return grant_micros, tier_name


def _naive_utc_from_timestamp(ts: int | None) -> datetime | None:
    """Convert a Unix timestamp to a naive UTC datetime (for DB storage)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@register("customer.subscription.created")
async def handle_subscription_created(event: stripe.Event, db: AsyncSession) -> None:
    """Upsert subscriptions row when a Stripe Subscription is created.

    When BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=True, also extracts Price
    metadata and grants a subscription_grant ledger entry.

    When BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False (default), only upserts
    the subscriptions row — balance_ledger is not touched.

    Idempotency: the stripe_events INSERT at the route level prevents this
    handler from running twice for the same event.id.

    Defensive: logs warning + returns (no raise) on missing/invalid data.
    """
    sub = event.data.object

    user_id = _extract_user_id(sub, event.id)
    if user_id is None:
        return

    grants_enabled = settings.BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER

    # Extract Price from sub.items — Stripe's subscription.items is a ListObject
    # ({data: [...], has_more, ...}). Access via .data first; fall back to dict-style
    # access for resilience across Stripe SDK versions.
    try:
        items_data = sub.items.data if hasattr(sub.items, "data") else sub["items"]["data"]
        if not items_data:
            raise IndexError("empty items.data")
        price_obj = (
            items_data[0].price if hasattr(items_data[0], "price") else items_data[0]["price"]
        )
    except (AttributeError, IndexError, KeyError, TypeError):
        logger.warning(
            "handle_subscription_created: could not extract price from sub.items",
            extra={"event_id": event.id, "sub_id": getattr(sub, "id", None)},
        )
        return

    tier_key = (
        getattr(price_obj, "lookup_key", None)
        or (price_obj.get("lookup_key") if hasattr(price_obj, "get") else None)
        or ""
    )
    stripe_price_id = (
        getattr(price_obj, "id", None)
        or (price_obj.get("id") if hasattr(price_obj, "get") else None)
        or ""
    )
    stripe_sub_id = getattr(sub, "id", "") or ""
    status = getattr(sub, "status", "incomplete") or "incomplete"
    cancel_at_period_end = bool(getattr(sub, "cancel_at_period_end", False))
    period_start = _naive_utc_from_timestamp(getattr(sub, "current_period_start", None))
    period_end = _naive_utc_from_timestamp(getattr(sub, "current_period_end", None))

    if grants_enabled:
        price_meta = _extract_price_metadata(price_obj, event.id)
        if price_meta is None:
            return
        grant_micros, tier_name = price_meta
    else:
        # Flag OFF: read tier_name for display; grant_micros defaults to 0 (unused).
        price_meta_raw = getattr(price_obj, "metadata", None) or {}
        tier_name = (
            price_meta_raw.get("tier_name") if hasattr(price_meta_raw, "get") else None
        ) or ""
        grant_micros = 0

    # Upsert subscriptions row keyed on user_id (one subscription per user).
    stmt = (
        pg_insert(Subscription)
        .values(
            user_id=user_id,
            stripe_subscription_id=stripe_sub_id,
            stripe_price_id=stripe_price_id,
            tier_key=tier_key,
            tier_name=tier_name,
            status=status,
            grant_micros=grant_micros,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "stripe_subscription_id": stripe_sub_id,
                "stripe_price_id": stripe_price_id,
                "tier_key": tier_key,
                "tier_name": tier_name,
                "status": status,
                "grant_micros": grant_micros,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "cancel_at_period_end": cancel_at_period_end,
            },
        )
    )
    await db.execute(stmt)

    if not grants_enabled:
        logger.info(
            "handle_subscription_created: upserted row (grants disabled — BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False)",
            extra={"event_id": event.id, "user_id": user_id, "stripe_sub_id": stripe_sub_id},
        )
        return

    # Grant initial period balance.
    await grant(
        user_id=user_id,
        amount_micros=grant_micros,
        reason=Reason.SUBSCRIPTION_GRANT,
        db=db,
        stripe_event_id=event.id,
    )

    logger.info(
        "handle_subscription_created: upserted row + granted subscription_grant",
        extra={
            "event_id": event.id,
            "user_id": user_id,
            "stripe_sub_id": stripe_sub_id,
            "grant_micros": grant_micros,
        },
    )


@register("customer.subscription.updated")
async def handle_subscription_updated(event: stripe.Event, db: AsyncSession) -> None:
    """Sync subscription state on update.

    Updates status, period timestamps, cancel_at_period_end, tier metadata
    (in case of plan change / upgrade / downgrade). Does NOT post a ledger
    entry — only subscription.created and invoice.paid do that.

    If the subscriptions row doesn't exist (edge case: updated fires before
    created), logs a warning and returns; the created event should reconcile.
    """
    sub = event.data.object

    user_id = _extract_user_id(sub, event.id)
    if user_id is None:
        return

    # Look up existing subscription row.
    result = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        logger.warning(
            "handle_subscription_updated: no subscriptions row for user_id=%s; "
            "created event may not have fired yet — ignoring",
            user_id,
            extra={"event_id": event.id},
        )
        return

    # Re-read Price metadata (plan may have changed). Use both attribute- and
    # dict-style access for resilience across Stripe SDK versions / event shapes.
    try:
        items_data = sub.items.data if hasattr(sub.items, "data") else sub["items"]["data"]
        if not items_data:
            raise IndexError("empty items.data")
        price_obj = (
            items_data[0].price if hasattr(items_data[0], "price") else items_data[0]["price"]
        )
        price_meta = _extract_price_metadata(price_obj, event.id)
    except (AttributeError, IndexError, KeyError, TypeError):
        price_obj = None
        price_meta = None

    row.status = getattr(sub, "status", row.status) or row.status
    row.cancel_at_period_end = bool(getattr(sub, "cancel_at_period_end", row.cancel_at_period_end))
    row.current_period_start = (
        _naive_utc_from_timestamp(getattr(sub, "current_period_start", None))
        or row.current_period_start
    )
    row.current_period_end = (
        _naive_utc_from_timestamp(getattr(sub, "current_period_end", None))
        or row.current_period_end
    )

    if price_obj is not None and price_meta is not None:
        grant_micros, tier_name = price_meta
        row.tier_key = getattr(price_obj, "lookup_key", row.tier_key) or row.tier_key
        row.stripe_price_id = getattr(price_obj, "id", row.stripe_price_id) or row.stripe_price_id
        row.grant_micros = grant_micros
        row.tier_name = tier_name

    db.add(row)

    logger.info(
        "handle_subscription_updated: synced state",
        extra={
            "event_id": event.id,
            "user_id": user_id,
            "status": row.status,
            "cancel_at_period_end": row.cancel_at_period_end,
        },
    )


@register("customer.subscription.deleted")
async def handle_subscription_deleted(event: stripe.Event, db: AsyncSession) -> None:
    """Mark subscription as cancelled when Stripe deletes it.

    Does NOT revoke already-granted balance — per payments.md §Cancellation,
    already-granted balance is the user's to keep. The row is retained for audit.
    """
    sub = event.data.object

    user_id = _extract_user_id(sub, event.id)
    if user_id is None:
        return

    result = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        logger.warning(
            "handle_subscription_deleted: no subscriptions row for user_id=%s",
            user_id,
            extra={"event_id": event.id},
        )
        return

    row.status = "cancelled"
    db.add(row)

    logger.info(
        "handle_subscription_deleted: marked cancelled",
        extra={"event_id": event.id, "user_id": user_id},
    )


@register("invoice.paid")
async def handle_invoice_paid(event: stripe.Event, db: AsyncSession) -> None:
    """Handle invoice.paid — grant subscription_reset on renewal cycles.

    billing_reason=subscription_create → no-op (subscription.created already granted).
    billing_reason=subscription_cycle → post subscription_reset (+grant_micros).
    Other billing_reason values (manual, threshold, update, etc.) → log + no-op.

    V1 simplification: subscription_reset is a positive grant of the new period's
    grant_micros. The strict "zero remaining prior-period grant + grant new period"
    semantics described in payments.md §Reason vocabulary are deferred to a follow-up
    ticket (the accounting is additive which is what users expect: balance increases
    monotonically across renewals).

    Also updates current_period_* from the invoice object.
    """
    invoice = event.data.object

    billing_reason = getattr(invoice, "billing_reason", None)

    if billing_reason == "subscription_create":
        # subscription.created already fired the subscription_grant — no-op here.
        logger.info(
            "handle_invoice_paid: billing_reason=subscription_create — no-op (grant already fired on subscription.created)",
            extra={"event_id": event.id},
        )
        return

    if billing_reason != "subscription_cycle":
        # Manual, threshold, update, proration — log and ignore.
        logger.info(
            "handle_invoice_paid: billing_reason=%r — no-op (not a renewal cycle)",
            billing_reason,
            extra={"event_id": event.id},
        )
        return

    # Renewal: find the subscription row via the invoice's subscription_id.
    stripe_sub_id = getattr(invoice, "subscription", None)
    if not stripe_sub_id:
        logger.warning(
            "handle_invoice_paid: billing_reason=subscription_cycle but invoice.subscription is missing",
            extra={"event_id": event.id},
        )
        return

    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        logger.warning(
            "handle_invoice_paid: no subscriptions row for stripe_sub_id=%s",
            stripe_sub_id,
            extra={"event_id": event.id},
        )
        return

    # Update period timestamps from the invoice's period.
    lines = getattr(invoice, "lines", None)
    if lines:
        try:
            line = lines.data[0]
            period_start = _naive_utc_from_timestamp(getattr(line.period, "start", None))
            period_end = _naive_utc_from_timestamp(getattr(line.period, "end", None))
            if period_start:
                row.current_period_start = period_start
            if period_end:
                row.current_period_end = period_end
        except (AttributeError, IndexError):
            pass  # non-fatal; period update is best-effort

    db.add(row)

    if not settings.BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER:
        logger.info(
            "handle_invoice_paid: updated period timestamps (grants disabled — BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False)",
            extra={"event_id": event.id, "user_id": row.user_id, "stripe_sub_id": stripe_sub_id},
        )
        return

    # Grant new period's balance.
    await grant(
        user_id=row.user_id,
        amount_micros=row.grant_micros,
        reason=Reason.SUBSCRIPTION_RESET,
        db=db,
        stripe_event_id=event.id,
    )

    logger.info(
        "handle_invoice_paid: granted subscription_reset for renewal",
        extra={
            "event_id": event.id,
            "user_id": row.user_id,
            "stripe_sub_id": stripe_sub_id,
            "grant_micros": row.grant_micros,
        },
    )


@register("invoice.payment_failed")
async def handle_invoice_payment_failed(event: stripe.Event, db: AsyncSession) -> None:
    """Mark subscription past_due when an invoice payment fails.

    Per payments.md §Past-due behavior: balance remains fully spendable.
    No new subscription grants fire until Stripe dunning resolves or the
    subscription is cancelled.
    """
    invoice = event.data.object

    stripe_sub_id = getattr(invoice, "subscription", None)
    if not stripe_sub_id:
        logger.warning(
            "handle_invoice_payment_failed: invoice.subscription is missing",
            extra={"event_id": event.id},
        )
        return

    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        logger.warning(
            "handle_invoice_payment_failed: no subscriptions row for stripe_sub_id=%s",
            stripe_sub_id,
            extra={"event_id": event.id},
        )
        return

    row.status = "past_due"
    db.add(row)

    logger.info(
        "handle_invoice_payment_failed: marked past_due",
        extra={"event_id": event.id, "user_id": row.user_id, "stripe_sub_id": stripe_sub_id},
    )
