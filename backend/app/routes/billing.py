"""Billing routes.

POST /billing/webhook — Stripe webhook receiver (signature-verified, idempotent).
POST /billing/topup   — Create Stripe PaymentIntent for a PAYG topup.
GET  /billing/balance — Return current balance for the authenticated user.

Note: do NOT add `from __future__ import annotations` — it breaks FastAPI's
Pydantic body-type resolution at runtime.
"""

import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.billing.handlers.topup  # noqa: F401 — registers payment_intent.succeeded handler
from app.billing import create_customer, format_balance, get_balance_micros
from app.billing.handlers import EVENT_HANDLERS
from app.billing.stripe_client import init_stripe, stripe
from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.dependencies import get_current_user, require_verified
from app.errors import validation_error
from app.models.stripe_event import StripeEvent
from app.routes.auth import (
    limiter,
)  # shared limiter instance (known chassis coupling — factor to app/rate_limit.py in 0018 audit)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TopupRequest(BaseModel):
    amount_micros: int


class TopupResponse(BaseModel):
    client_secret: str
    amount_micros: int


class BalanceResponse(BaseModel):
    balance_micros: int
    formatted: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Response:
    """Receive and process Stripe webhook events.

    Verifies the Stripe signature on every inbound request. Checks the
    stripe_events table for idempotency — duplicate event ids return 200
    without reprocessing. Dispatches to registered handlers via EVENT_HANDLERS;
    unregistered event types log a warning and return 200.

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
            # Atomic idempotency: try to claim this event_id by inserting it.
            # If another concurrent request already inserted (race) or this is a
            # plain duplicate retry, ON CONFLICT DO NOTHING returns rowcount=0
            # and we short-circuit. Postgres serialises concurrent INSERT-with-
            # conflict at the row-lock level, so exactly one transaction "owns"
            # the event id and the handler runs exactly once.
            stmt = (
                pg_insert(StripeEvent)
                .values(id=event.id, event_type=event.type)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            result = await db.execute(stmt)
            if result.rowcount == 0:
                logger.info(
                    "stripe_webhook_duplicate_event",
                    extra={"event_id": event.id, "event_type": event.type},
                )
                return Response(status_code=200)

            # We own this event id. Dispatch the registered handler, if any.
            # The handler runs inside the same transaction as the stripe_events
            # INSERT, so a handler exception rolls back BOTH the handler's
            # writes (e.g., balance_ledger row in topup handler) AND the
            # stripe_events row — Stripe will then retry the event cleanly.
            handler = EVENT_HANDLERS.get(event.type)
            if handler:
                await handler(event, db)
            else:
                logger.warning("Unhandled Stripe event type: %s", event.type)

    return Response(status_code=200)


@router.post("/topup", response_model=TopupResponse)
@limiter.limit(settings.TOPUP_RATE_LIMIT)
async def topup(
    request: Request,
    body: TopupRequest,
    user=Depends(require_verified),
    db: AsyncSession = Depends(get_db),
) -> TopupResponse:
    """Create a Stripe PaymentIntent for a PAYG topup.

    Requires a verified user. Validates amount within configured bounds.
    Lazily creates a Stripe Customer if the user has none. Returns a
    client_secret for the frontend to confirm via Stripe Elements.
    """
    init_stripe()

    # Validate amount bounds.
    if body.amount_micros < settings.BILLING_TOPUP_MIN_MICROS:
        min_dollars = settings.BILLING_TOPUP_MIN_MICROS / 1_000_000
        raise validation_error(f"Amount below minimum ${min_dollars:.2f}.")

    if body.amount_micros > settings.BILLING_TOPUP_MAX_MICROS:
        max_dollars = settings.BILLING_TOPUP_MAX_MICROS / 1_000_000
        raise validation_error(f"Amount above maximum ${max_dollars:.2f}.")

    # Lazy Customer creation: if the user has no Stripe Customer, create one now.
    if user.stripe_customer_id is None:
        customer_id = await create_customer(user, db)
        user.stripe_customer_id = customer_id
        # Flush so the updated stripe_customer_id is visible within the session.
        await db.flush()

    # Idempotency key: same user + amount + minute window = same PaymentIntent.
    idempotency_key = f"topup:{user.id}:{body.amount_micros}:{int(time.time() // 60)}"

    # Stripe amount is in cents; micros / 10_000 = cents.
    kwargs = {
        "customer": user.stripe_customer_id,
        "amount": body.amount_micros // 10_000,
        "currency": settings.BILLING_CURRENCY,
        "metadata": {"user_id": str(user.id)},
    }
    if settings.STRIPE_TAX_ENABLED:
        kwargs["automatic_tax"] = {"enabled": True}

    intent = stripe.PaymentIntent.create(**kwargs, idempotency_key=idempotency_key)

    return TopupResponse(client_secret=intent.client_secret, amount_micros=body.amount_micros)


@router.get("/balance", response_model=BalanceResponse)
async def balance(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """Return the current balance for the authenticated user.

    Authed only — NOT verified-gated. Unverified users can see their balance
    (which may be $0.00 or include a signup bonus) without hitting a 403.
    """
    balance_micros = await get_balance_micros(user.id, db)
    return BalanceResponse(
        balance_micros=balance_micros,
        formatted=format_balance(balance_micros),
    )
