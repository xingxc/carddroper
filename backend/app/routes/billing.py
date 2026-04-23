"""Billing routes.

POST /billing/webhook — Stripe webhook receiver (signature-verified, idempotent).

Note: do NOT add `from __future__ import annotations` — it breaks FastAPI's
Pydantic body-type resolution at runtime.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from app.billing.stripe_client import init_stripe, stripe
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.stripe_event import StripeEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Response:
    """Receive and process Stripe webhook events.

    Verifies the Stripe signature on every inbound request. Checks the
    stripe_events table for idempotency — duplicate event ids return 200
    without reprocessing. All event types unrecognized in this ticket
    (0021 scope) log a warning and return 200; specific handlers land in
    later tickets.

    Returns:
        200 on success (including idempotent replays).
        400 on invalid or missing Stripe signature.
    """
    init_stripe()
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except (stripe.error.SignatureVerificationError, ValueError):
        logger.warning("stripe_webhook_invalid_signature")
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_SIGNATURE",
                    "message": "Invalid Stripe webhook signature.",
                }
            },
        )

    async with AsyncSessionLocal() as db:
        async with db.begin():
            # Idempotency: return 200 immediately if already processed.
            existing = await db.execute(select(StripeEvent).where(StripeEvent.id == event.id))
            if existing.scalar_one_or_none() is not None:
                logger.info(
                    "stripe_webhook_duplicate_event",
                    extra={"event_id": event.id, "event_type": event.type},
                )
                return Response(status_code=200)

            # Dispatch — all event types are unhandled in 0021 scope.
            # Specific handlers (payment_intent.succeeded, subscription events,
            # invoice.*) land in later tickets (0022, 0023).
            logger.warning("Unhandled Stripe event type: %s", event.type)

            # Record the event before returning 200.
            db.add(StripeEvent(id=event.id, event_type=event.type))

    return Response(status_code=200)
