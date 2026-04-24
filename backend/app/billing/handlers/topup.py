"""Handler for payment_intent.succeeded — grants topup balance.

Registered via the dispatch registry in app.billing.handlers.
The side-effect import that causes this module to load lives in
routes/billing.py, not in handlers/__init__.py.
"""

import logging

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.handlers import register
from app.billing.primitives import grant
from app.billing.reason import Reason

logger = logging.getLogger(__name__)


@register("payment_intent.succeeded")
async def handle_payment_intent_succeeded(event: stripe.Event, db: AsyncSession) -> None:
    """Grant topup balance when a PaymentIntent for a topup succeeds.

    Extracts user_id from metadata, converts Stripe cents to micros,
    and writes a ledger entry. Idempotency is handled upstream by the
    stripe_events table — this function is never called for a replayed event.

    Defensive: logs warning + returns (no raise) on missing or invalid data
    so a malformed event never crashes the webhook endpoint.
    """
    pi = event.data.object

    # Extract user_id from metadata.
    # Use getattr with default {} because Stripe StripeObject raises AttributeError
    # (not KeyError) on missing keys, and metadata may be absent entirely.
    metadata = getattr(pi, "metadata", None) or {}
    raw_user_id = metadata.get("user_id") if hasattr(metadata, "get") else None
    if not raw_user_id:
        logger.warning(
            "handle_payment_intent_succeeded: missing metadata.user_id",
            extra={"event_id": event.id},
        )
        return

    try:
        user_id = int(raw_user_id)
    except (ValueError, TypeError):
        logger.warning(
            "handle_payment_intent_succeeded: invalid metadata.user_id=%r",
            raw_user_id,
            extra={"event_id": event.id},
        )
        return

    # Extract amount (cents from Stripe).
    amount_cents = getattr(pi, "amount", None)
    if not amount_cents or amount_cents <= 0:
        logger.warning(
            "handle_payment_intent_succeeded: missing or zero amount",
            extra={"event_id": event.id, "amount": amount_cents},
        )
        return

    # Convert cents → micros (1 cent = 10_000 micros).
    amount_micros = amount_cents * 10_000

    await grant(
        user_id=user_id,
        amount_micros=amount_micros,
        reason=Reason.TOPUP,
        db=db,
        stripe_event_id=event.id,
    )

    logger.info(
        "handle_payment_intent_succeeded: granted topup",
        extra={
            "event_id": event.id,
            "user_id": user_id,
            "amount_micros": amount_micros,
        },
    )
