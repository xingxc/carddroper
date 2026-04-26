"""Billing routes.

POST /billing/webhook      — Stripe webhook receiver (signature-verified, idempotent).
POST /billing/topup        — Create Stripe PaymentIntent for a PAYG topup.
GET  /billing/balance      — Return current balance for the authenticated user.
POST /billing/setup-intent — Create Stripe SetupIntent for collecting a payment method.
POST /billing/subscribe    — Create a Stripe Subscription for the authenticated user.
GET  /billing/subscription — Return current subscription state for the authenticated user.
GET  /billing/tiers        — Return enriched tier envelopes for the given lookup_keys (CSV).

Note: do NOT add `from __future__ import annotations` — it breaks FastAPI's
Pydantic body-type resolution at runtime.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.billing.handlers.subscription  # noqa: F401 — registers 5 subscription/invoice events
import app.billing.handlers.topup  # noqa: F401 — registers payment_intent.succeeded handler
from app.billing import create_customer, format_balance, format_price, get_balance_micros
from app.billing.handlers import EVENT_HANDLERS
from app.billing.stripe_client import init_stripe, stripe
from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.dependencies import get_current_user, require_verified
from app.errors import conflict, not_found, validation_error
from app.models.stripe_event import StripeEvent
from app.models.subscription import Subscription
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


class SetupIntentResponse(BaseModel):
    client_secret: str


class SubscribeRequest(BaseModel):
    price_lookup_key: str
    payment_method_id: str


class SubscribeResponse(BaseModel):
    subscription_id: str
    status: str
    requires_action: bool
    client_secret: Optional[str] = None


class SubscriptionResponse(BaseModel):
    has_subscription: bool
    tier_key: Optional[str] = None
    tier_name: Optional[str] = None
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False


class TierEnvelope(BaseModel):
    lookup_key: str
    tier_name: str
    description: Optional[str] = None
    price_display: str
    amount_cents: int
    currency: str
    interval: str
    interval_count: int
    grant_micros: int


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


@router.post("/setup-intent", response_model=SetupIntentResponse)
async def setup_intent(
    request: Request,
    user=Depends(require_verified),
    db: AsyncSession = Depends(get_db),
) -> SetupIntentResponse:
    """Create a Stripe SetupIntent for collecting a payment method.

    Requires a verified user. Lazily creates a Stripe Customer if the user
    has none. Returns a client_secret for the frontend to confirm via Stripe
    Elements.

    Idempotency: one SetupIntent per user per minute (minute-window key).
    """
    init_stripe()

    # Lazy Customer creation.
    if user.stripe_customer_id is None:
        customer_id = await create_customer(user, db)
        user.stripe_customer_id = customer_id
        await db.flush()

    idempotency_key = f"setup:{user.id}:{int(time.time() // 60)}"

    si = stripe.SetupIntent.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        usage="off_session",
        idempotency_key=idempotency_key,
    )

    return SetupIntentResponse(client_secret=si.client_secret)


@router.post("/subscribe", response_model=SubscribeResponse)
@limiter.limit(settings.SUBSCRIBE_RATE_LIMIT)
async def subscribe(
    request: Request,
    body: SubscribeRequest,
    user=Depends(require_verified),
    db: AsyncSession = Depends(get_db),
) -> SubscribeResponse:
    """Create a Stripe Subscription for the authenticated user.

    Requires a verified user. Rate-limited to SUBSCRIBE_RATE_LIMIT per IP.

    Steps:
    1. Resolve Stripe Price by lookup_key; read tier metadata.
    2. Attach payment method + set as customer default.
    3. Reject if user already has an active/trialing/past_due subscription (409).
    4. Create Stripe Subscription.
    5. Upsert subscriptions row. Balance grant deferred to webhook handler.
    6. Return subscription state including requires_action flag for 3DS.
    """
    init_stripe()

    # 1. Resolve Price by lookup_key.
    prices_list = stripe.Price.list(
        lookup_keys=[body.price_lookup_key],
        expand=["data.product"],
    )
    if hasattr(prices_list, "auto_paging_iter"):
        prices = list(prices_list.auto_paging_iter())
    else:
        prices = list(prices_list.data) if hasattr(prices_list, "data") else []
    if not prices:
        raise not_found(f"Price with lookup_key={body.price_lookup_key!r}")

    price = prices[0]

    # 2. Read required Price metadata.
    metadata = getattr(price, "metadata", None) or {}
    raw_grant = metadata.get("grant_micros") if hasattr(metadata, "get") else None
    tier_name = metadata.get("tier_name") if hasattr(metadata, "get") else None

    if not raw_grant:
        raise validation_error(f"Price {body.price_lookup_key!r} is missing metadata.grant_micros")
    if not tier_name:
        raise validation_error(f"Price {body.price_lookup_key!r} is missing metadata.tier_name")

    try:
        grant_micros = int(raw_grant)
    except (ValueError, TypeError):
        raise validation_error(
            f"Price {body.price_lookup_key!r} has invalid metadata.grant_micros={raw_grant!r}"
        )

    tier_key = getattr(price, "lookup_key", "") or body.price_lookup_key

    # Lazy Customer creation.
    if user.stripe_customer_id is None:
        customer_id = await create_customer(user, db)
        user.stripe_customer_id = customer_id
        await db.flush()

    # 3. Attach payment method + set as customer default.
    stripe.PaymentMethod.attach(body.payment_method_id, customer=user.stripe_customer_id)
    stripe.Customer.modify(
        user.stripe_customer_id,
        invoice_settings={"default_payment_method": body.payment_method_id},
    )

    # 4. Check for existing active subscription.
    result = await db.execute(select(Subscription).where(Subscription.user_id == user.id))
    existing = result.scalar_one_or_none()
    if existing and existing.status in ("active", "trialing", "past_due"):
        raise conflict(f"ALREADY_SUBSCRIBED: user already has a {existing.status!r} subscription")

    # 5. Create Stripe Subscription.
    kwargs = {
        "customer": user.stripe_customer_id,
        "items": [{"price": price.id}],
        "default_payment_method": body.payment_method_id,
        "metadata": {"user_id": str(user.id)},
        "expand": ["latest_invoice.payment_intent"],
    }
    if settings.STRIPE_TAX_ENABLED:
        kwargs["automatic_tax"] = {"enabled": True}

    sub = stripe.Subscription.create(
        **kwargs,
        idempotency_key=f"subscribe:{user.id}:{body.price_lookup_key}",
    )

    sub_status = getattr(sub, "status", "incomplete") or "incomplete"
    cancel_at_period_end = bool(getattr(sub, "cancel_at_period_end", False))

    period_start = None
    period_end = None
    raw_start = getattr(sub, "current_period_start", None)
    raw_end = getattr(sub, "current_period_end", None)
    if raw_start:
        period_start = datetime.fromtimestamp(raw_start, tz=timezone.utc).replace(tzinfo=None)
    if raw_end:
        period_end = datetime.fromtimestamp(raw_end, tz=timezone.utc).replace(tzinfo=None)

    # 6. Upsert subscriptions row. Balance grant deferred to webhook (subscription.created).
    upsert_stmt = (
        pg_insert(Subscription)
        .values(
            user_id=user.id,
            stripe_subscription_id=sub.id,
            stripe_price_id=price.id,
            tier_key=tier_key,
            tier_name=tier_name,
            status=sub_status,
            grant_micros=grant_micros,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "stripe_subscription_id": sub.id,
                "stripe_price_id": price.id,
                "tier_key": tier_key,
                "tier_name": tier_name,
                "status": sub_status,
                "grant_micros": grant_micros,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "cancel_at_period_end": cancel_at_period_end,
            },
        )
    )
    await db.execute(upsert_stmt)

    # 7. Determine if 3DS is required.
    requires_action = sub_status == "incomplete"
    client_secret = None
    if requires_action:
        try:
            client_secret = sub.latest_invoice.payment_intent.client_secret
        except AttributeError:
            pass

    logger.info(
        "subscribe: created subscription",
        extra={
            "user_id": user.id,
            "stripe_sub_id": sub.id,
            "status": sub_status,
            "tier_key": tier_key,
            "requires_action": requires_action,
        },
    )

    return SubscribeResponse(
        subscription_id=sub.id,
        status=sub_status,
        requires_action=requires_action,
        client_secret=client_secret,
    )


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Return the current subscription state for the authenticated user.

    Authed only — NOT verified-gated. Returns has_subscription=False when:
    - No subscriptions row exists.
    - Row exists but status='cancelled' (cancelled = no active subscription;
      row is kept for audit).
    """
    result = await db.execute(select(Subscription).where(Subscription.user_id == user.id))
    row = result.scalar_one_or_none()

    if row is None or row.status == "cancelled":
        return SubscriptionResponse(
            has_subscription=False,
            tier_key=None,
            tier_name=None,
            status=None,
            current_period_end=None,
            cancel_at_period_end=False,
        )

    return SubscriptionResponse(
        has_subscription=True,
        tier_key=row.tier_key,
        tier_name=row.tier_name,
        status=row.status,
        current_period_end=row.current_period_end,
        cancel_at_period_end=row.cancel_at_period_end,
    )


@router.get("/tiers", response_model=list[TierEnvelope])
async def list_tiers(
    lookup_keys: str = "",
    user=Depends(get_current_user),
) -> list[TierEnvelope]:
    """Return enriched tier envelopes for the requested Stripe Price lookup_keys.

    lookup_keys: comma-separated list of Stripe Price lookup_keys.
    Empty or absent param returns an empty list (chassis no-op for projects
    that don't surface subscriptions).

    Authed only (not verified-gated) — matches GET /billing/subscription.

    For each Price returned by Stripe:
    - If metadata.tier_name or metadata.grant_micros is missing, the tier is
      skipped and a structured warning is logged.
    - If the Price currency is not USD, a warning is logged; the tier is still
      returned with a "$" prefix (chassis USD-only policy for v1).

    Response preserves the input order of lookup_keys.
    """
    init_stripe()

    keys = [k.strip() for k in lookup_keys.split(",") if k.strip()]
    if not keys:
        return []

    prices_result = await asyncio.to_thread(
        stripe.Price.list,
        lookup_keys=keys,
        active=True,
        expand=["data.product"],
        limit=100,
    )

    raw_prices = prices_result.data if hasattr(prices_result, "data") else []

    envelopes: list[TierEnvelope] = []
    for price in raw_prices:
        metadata = getattr(price, "metadata", None) or {}

        # Soft skip — missing required metadata makes this tier unsubscribable anyway.
        try:
            tier_name = metadata["tier_name"]
        except KeyError:
            logger.warning(
                "tiers_skip_missing_metadata",
                extra={"lookup_key": getattr(price, "lookup_key", None), "missing": "tier_name"},
            )
            continue

        try:
            grant_micros = int(metadata["grant_micros"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "tiers_skip_missing_metadata",
                extra={"lookup_key": getattr(price, "lookup_key", None), "missing": str(exc)},
            )
            continue

        currency = getattr(price, "currency", "usd") or "usd"
        if currency.lower() != "usd":
            logger.warning(
                "tiers_non_usd_currency",
                extra={"lookup_key": getattr(price, "lookup_key", None), "currency": currency},
            )

        recurring = getattr(price, "recurring", None) or {}
        if hasattr(recurring, "get"):
            interval = recurring.get("interval", "month")
            interval_count = recurring.get("interval_count", 1)
        else:
            interval = getattr(recurring, "interval", "month")
            interval_count = getattr(recurring, "interval_count", 1)

        # Product description: Price.product is expanded to the full Product object.
        product = getattr(price, "product", None)
        description: Optional[str] = None
        if product is not None:
            description = getattr(product, "description", None) or None

        envelopes.append(
            TierEnvelope(
                lookup_key=getattr(price, "lookup_key", ""),
                tier_name=tier_name,
                description=description,
                price_display=format_price(
                    getattr(price, "unit_amount", 0) or 0,
                    currency,
                    interval,
                    interval_count,
                ),
                amount_cents=getattr(price, "unit_amount", 0) or 0,
                currency=currency,
                interval=interval,
                interval_count=interval_count,
                grant_micros=grant_micros,
            )
        )

    # Preserve input order — Stripe does not guarantee ordering.
    by_key = {e.lookup_key: e for e in envelopes}
    return [by_key[k] for k in keys if k in by_key]
