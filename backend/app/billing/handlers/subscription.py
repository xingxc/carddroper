"""Handlers for subscription lifecycle + invoice webhook events.

Registered via the dispatch registry in app.billing.handlers.
The side-effect import that causes this module to load lives in
routes/billing.py, not in handlers/__init__.py.

Handles:
- customer.subscription.created  — upsert subscriptions row only; NO ledger write
- customer.subscription.updated  — sync state (status, period, cancel flag, tier metadata)
- customer.subscription.deleted  — mark status='cancelled'; do NOT revoke balance
- invoice.paid                   — subscription_create → grant subscription_grant (flag-gated)
                                   subscription_cycle → grant subscription_reset (flag-gated)
- invoice.payment_failed         — mark status='past_due'

Architectural principle (ticket 0024.11): grants are coupled to invoice.paid events,
never to customer.subscription.created. invoice.paid has guaranteed semantics — the
invoice was paid, money moved. customer.subscription.created can fire before the first
invoice is paid (3DS pending, decline pending); coupling grants to it produces phantom
ledger entries on payment-failure paths.
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


def _naive_utc_from_timestamp(ts: int | float | None) -> datetime | None:
    """Convert a Unix timestamp to a naive UTC datetime (for DB storage)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


def _extract_period_timestamps(obj) -> tuple[datetime | None, datetime | None]:
    """Extract current_period_start and current_period_end from a Stripe object.

    Uses defensive dual-access: attribute access first, then dict-style access.
    StripeObject is dict-backed; attribute access via __getattr__ works for most
    fields, but dict-method-name collisions can cause __getattr__ to return the
    dict method instead of the field value. Belt-and-suspenders guards against that.
    """
    raw_start = getattr(obj, "current_period_start", None) or (
        obj.get("current_period_start") if hasattr(obj, "get") else None
    )
    raw_end = getattr(obj, "current_period_end", None) or (
        obj.get("current_period_end") if hasattr(obj, "get") else None
    )
    return _naive_utc_from_timestamp(raw_start), _naive_utc_from_timestamp(raw_end)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@register("customer.subscription.created")
async def handle_subscription_created(event: stripe.Event, db: AsyncSession) -> None:
    """Upsert subscriptions row when a Stripe Subscription is created.

    This handler does NOT post a ledger entry, regardless of
    BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER. Grants are coupled to invoice.paid
    (billing_reason=subscription_create), not to customer.subscription.created.
    Per ticket 0024.11: customer.subscription.created can fire before the first
    invoice is paid (3DS pending, decline pending), so coupling grants to it
    produces phantom ledger entries on payment-failure paths.

    The handler retains:
    - Status / lifecycle upsert (always)
    - Path B period write on INSERT (subscribe endpoint is authoritative on UPDATE — ticket 0024.5)
    - Path B grant_micros write on INSERT (subscribe endpoint is authoritative on UPDATE — ticket 0024.7)

    Idempotency: the stripe_events INSERT at the route level prevents this
    handler from running twice for the same event.id.

    Defensive: logs warning + returns (no raise) on missing/invalid data.
    """
    sub = event.data.object

    user_id = _extract_user_id(sub, event.id)
    if user_id is None:
        return

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
    # Defensive dual-access for period timestamps (see _extract_period_timestamps docstring).
    period_start, period_end = _extract_period_timestamps(sub)

    # Read tier_name (always needed for display); grant_micros for INSERT-only default.
    # grant_micros is read from metadata regardless of flag state so the INSERT case (rare
    # out-of-band subscription created before subscribe endpoint runs) has a reasonable value.
    # When flag=OFF the subscribe endpoint will have already stored 0; the INSERT branch is
    # only exercised for truly out-of-band subscriptions.
    price_meta_raw = getattr(price_obj, "metadata", None) or {}
    tier_name = (
        price_meta_raw.get("tier_name") if hasattr(price_meta_raw, "get") else None
    ) or ""
    raw_grant = (
        price_meta_raw.get("grant_micros") if hasattr(price_meta_raw, "get") else None
    ) or "0"
    try:
        grant_micros_insert = int(raw_grant)
    except (ValueError, TypeError):
        grant_micros_insert = 0

    # Upsert subscriptions row keyed on user_id (one subscription per user).
    # NOTE: current_period_* and grant_micros are included in values= for the INSERT case
    # (rare out-of-band subscription created before subscribe endpoint runs) but intentionally
    # omitted from set_= in the UPDATE case.
    #
    # Sources of truth per field (Path B architectural model — tickets 0024.5, 0024.7):
    # - current_period_*: subscribe endpoint (initial) + invoice.paid cycle handler (renewals).
    # - grant_micros: subscribe endpoint (initial, flag-gated) + invoice.paid cycle handler
    #   when BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=True (handles mid-subscription tier changes).
    #
    # Keeping these writes here in the UPDATE path would overwrite the subscribe endpoint's
    # correctly flag-gated values with webhook-extracted values that ignore the chassis flag
    # (root cause of ticket 0024.7 for grant_micros; ticket 0024.5 for period fields).
    stmt = (
        pg_insert(Subscription)
        .values(
            user_id=user_id,
            stripe_subscription_id=stripe_sub_id,
            stripe_price_id=stripe_price_id,
            tier_key=tier_key,
            tier_name=tier_name,
            status=status,
            grant_micros=grant_micros_insert,
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
                # grant_micros deliberately omitted: subscribe endpoint is authoritative at
                # creation (writes 0 when flag=false; metadata value when flag=true); invoice.paid
                # cycle handler updates on renewal when flag=true. This handler must not overwrite
                # the endpoint's flag-gated value with the metadata value regardless of flag
                # (root cause of ticket 0024.7).
                # current_period_start and current_period_end deliberately omitted:
                # subscribe endpoint (initial) and invoice.paid cycle handler (renewals)
                # are authoritative (ticket 0024.5).
                "cancel_at_period_end": cancel_at_period_end,
            },
        )
    )
    await db.execute(stmt)

    # Grants are coupled to invoice.paid (billing_reason=subscription_create), NOT to
    # customer.subscription.created. Per ticket 0024.11: customer.subscription.created
    # can fire before the first invoice is paid (3DS pending, decline pending), so coupling
    # grants to it produces phantom ledger entries on payment-failure paths (e.g., user
    # fails the 3DS challenge for the subscription's first invoice, subscription remains
    # incomplete, but a subscription_grant ledger entry was already written — real value
    # leakage when flag=true). invoice.paid is the canonical "money moved" event.
    # The grant() call has been moved to handle_invoice_paid (subscription_create branch).

    logger.info(
        "handle_subscription_created: upserted row (grant deferred to invoice.paid subscription_create)",
        extra={"event_id": event.id, "user_id": user_id, "stripe_sub_id": stripe_sub_id},
    )


@register("customer.subscription.updated")
async def handle_subscription_updated(event: stripe.Event, db: AsyncSession) -> None:
    """Sync subscription state on update.

    Updates status, cancel_at_period_end, and tier metadata (in case of plan
    change / upgrade / downgrade). Does NOT update period timestamps — those are
    authoritative from the subscribe endpoint (initial) and invoice.paid cycle
    handler (renewals). Webhook payload period extraction is unreliable across
    Stripe API versions; see ticket 0024.5 for the full rationale.

    Does NOT post a ledger entry — only subscription.created and invoice.paid do that.

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
    # Period timestamps (current_period_start / current_period_end) are deliberately NOT
    # updated here. The subscribe endpoint is authoritative at creation; invoice.paid
    # (subscription_cycle) is authoritative at renewal. Webhook payload period extraction
    # is unreliable across Stripe API versions — see ticket 0024.5.

    if price_obj is not None and price_meta is not None:
        grant_micros, tier_name = price_meta
        row.tier_key = getattr(price_obj, "lookup_key", row.tier_key) or row.tier_key
        row.stripe_price_id = getattr(price_obj, "id", row.stripe_price_id) or row.stripe_price_id
        # grant_micros deliberately NOT written here (ticket 0024.7 Path B):
        # subscribe endpoint is authoritative at creation (flag-gated: 0 when flag=false,
        # metadata value when flag=true); invoice.paid cycle handler updates on renewal
        # when flag=true. This handler syncs status and cancel-flag only.
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
    """Handle invoice.paid — grant subscription_grant (initial) or subscription_reset (renewal).

    billing_reason=subscription_create → post subscription_grant when flag=true (ticket 0024.11).
        This is the canonical trigger for the initial-period grant. invoice.paid is the
        authoritative "money moved" event; customer.subscription.created is NOT used for grants
        because it can fire before the first invoice is paid (3DS pending, decline pending).
    billing_reason=subscription_cycle → post subscription_reset (+grant_micros) when flag=true.
    Other billing_reason values (manual, threshold, update, etc.) → log + no-op.

    V1 simplification: subscription_reset is a positive grant of the new period's
    grant_micros. The strict "zero remaining prior-period grant + grant new period"
    semantics described in payments.md §Reason vocabulary are deferred to a follow-up
    ticket (the accounting is additive which is what users expect: balance increases
    monotonically across renewals).

    On subscription_cycle: also writes current_period_start/end from
    invoice.lines.data[0].period.start/end — the canonical, API-version-stable
    source for renewal period boundaries (ticket 0024.5).
    """
    invoice = event.data.object

    billing_reason = getattr(invoice, "billing_reason", None)

    if billing_reason == "subscription_create":
        # Ticket 0024.11: the initial-period subscription_grant is now coupled to this event,
        # not to customer.subscription.created. invoice.paid guarantees money moved; the
        # subscription.created event fires regardless of whether the first invoice is paid
        # (3DS-pending, decline-pending paths produce phantom grants if grant() is called there).
        #
        # When flag=false: no ledger write; return immediately (period fields are written by
        # the subscribe endpoint at creation, not here — subscription_create invoices do not
        # update period timestamps; that is the subscription_cycle branch's job).
        if not settings.BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER:
            logger.info(
                "handle_invoice_paid: billing_reason=subscription_create — no-op (BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False)",
                extra={"event_id": event.id},
            )
            return

        # Resolve the subscriptions row via the invoice's subscription_id so we can read
        # grant_micros (the authoritative value set by the subscribe endpoint at creation).
        stripe_sub_id = getattr(invoice, "subscription", None)
        if not stripe_sub_id:
            logger.warning(
                "handle_invoice_paid: billing_reason=subscription_create but invoice.subscription is missing",
                extra={"event_id": event.id},
            )
            return

        result = await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            logger.warning(
                "handle_invoice_paid: billing_reason=subscription_create — no subscriptions row for stripe_sub_id=%s",
                stripe_sub_id,
                extra={"event_id": event.id},
            )
            return

        # Use the row's grant_micros as the primary source (set by the subscribe endpoint,
        # flag-gated: 0 when flag=false, Price.metadata.grant_micros when true). Fallback
        # to invoice.lines.data[0].price.metadata.grant_micros (matching the subscription_cycle
        # branch pattern) for resilience against out-of-band subscriptions where the row's
        # grant_micros may not have been set by the subscribe endpoint.
        amount_micros = row.grant_micros
        if amount_micros == 0:
            # Attempt fallback from invoice line-item price metadata.
            lines_data = (
                invoice.lines.data
                if hasattr(invoice, "lines") and hasattr(invoice.lines, "data")
                else (invoice.get("lines", {}).get("data", []) if hasattr(invoice, "get") else [])
            )
            if lines_data:
                try:
                    line = lines_data[0]
                    _absent = object()
                    _price = getattr(line, "price", _absent)
                    if _price is _absent:
                        price_obj = line.get("price") if hasattr(line, "get") else None
                    else:
                        price_obj = _price
                    if price_obj is not None:
                        _absent2 = object()
                        _meta = getattr(price_obj, "metadata", _absent2)
                        if _meta is _absent2:
                            price_meta = (
                                price_obj.get("metadata") if hasattr(price_obj, "get") else None
                            )
                        else:
                            price_meta = _meta
                        if price_meta is not None:
                            raw_grant = (
                                price_meta.get("grant_micros")
                                if hasattr(price_meta, "get")
                                else None
                            )
                            if raw_grant is not None:
                                try:
                                    amount_micros = int(raw_grant)
                                except (ValueError, TypeError):
                                    pass
                except (AttributeError, IndexError, TypeError):
                    pass

        await grant(
            user_id=row.user_id,
            amount_micros=amount_micros,
            reason=Reason.SUBSCRIPTION_GRANT,
            db=db,
            stripe_event_id=event.id,
        )

        logger.info(
            "handle_invoice_paid: granted subscription_grant for initial period",
            extra={
                "event_id": event.id,
                "user_id": row.user_id,
                "stripe_sub_id": stripe_sub_id,
                "grant_micros": amount_micros,
            },
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

    # Update period timestamps from the invoice line-item period.
    # invoice.lines.data[0].period.start/end is the authoritative renewal period source
    # (invoice schema is stable across Stripe API versions — ticket 0024.5).
    # Use defensive dual-access: attribute access first, then dict-style fallback.
    lines_data = (
        invoice.lines.data
        if hasattr(invoice, "lines") and hasattr(invoice.lines, "data")
        else (invoice.get("lines", {}).get("data", []) if hasattr(invoice, "get") else [])
    )
    new_period_start = None
    new_period_end = None
    if lines_data:
        try:
            line = lines_data[0]
            period = getattr(line, "period", None) or (
                line.get("period", {}) if hasattr(line, "get") else {}
            )
            raw_start = getattr(period, "start", None) or (
                period.get("start") if hasattr(period, "get") else None
            )
            raw_end = getattr(period, "end", None) or (
                period.get("end") if hasattr(period, "get") else None
            )
            new_period_start = _naive_utc_from_timestamp(raw_start)
            new_period_end = _naive_utc_from_timestamp(raw_end)
        except (AttributeError, IndexError, TypeError):
            pass  # non-fatal; period update is best-effort
    if new_period_start is not None:
        row.current_period_start = new_period_start
    if new_period_end is not None:
        row.current_period_end = new_period_end

    # Update grant_micros from the invoice line-item's price metadata when grants are enabled.
    # This is the authoritative renewal-time write for grant_micros (ticket 0024.7 Path B):
    # captures mid-subscription tier changes (e.g., Starter → Pro changes grant_micros from
    # 19990000 to 49990000; the next renewal invoice carries the new tier's metadata).
    # When flag=false, grant_micros is irrelevant (no ledger writes); leave it untouched.
    if settings.BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER and lines_data:
        try:
            line = lines_data[0]
            # Use sentinel to distinguish "attribute exists but is None" from "attribute absent".
            # The simple `getattr(..., None) or dict_fallback` pattern fires the dict fallback
            # even when the attribute is explicitly None, producing wrong results with MagicMocks
            # and any real Stripe object that has price=None (e.g., no line item price).
            _absent = object()
            _price = getattr(line, "price", _absent)
            if _price is _absent:
                price_obj = line.get("price") if hasattr(line, "get") else None
            else:
                price_obj = _price
            if price_obj is not None:
                _absent2 = object()
                _meta = getattr(price_obj, "metadata", _absent2)
                if _meta is _absent2:
                    price_meta = price_obj.get("metadata") if hasattr(price_obj, "get") else None
                else:
                    price_meta = _meta
                if price_meta is not None:
                    raw_grant = (
                        price_meta.get("grant_micros") if hasattr(price_meta, "get") else None
                    )
                    if raw_grant is not None:
                        try:
                            row.grant_micros = int(raw_grant)
                        except (ValueError, TypeError):
                            logger.warning(
                                "handle_invoice_paid: invalid price.metadata.grant_micros=%r — keeping existing row value",
                                raw_grant,
                                extra={"event_id": event.id},
                            )
        except (AttributeError, IndexError, TypeError):
            pass  # non-fatal; grant_micros update is best-effort; existing row value used for grant

    db.add(row)

    if not settings.BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER:
        logger.info(
            "handle_invoice_paid: updated period timestamps (grants disabled — BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False)",
            extra={"event_id": event.id, "user_id": row.user_id, "stripe_sub_id": stripe_sub_id},
        )
        return

    # Grant new period's balance using the (possibly updated) grant_micros.
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
