"""Thin wrapper around the Stripe SDK.

Keeps the SDK integration surface narrow and easy to monkey-patch in tests.
All billing code imports stripe from here rather than directly, so tests can
patch `app.billing.stripe_client.stripe` to intercept SDK calls.
"""

import stripe as _stripe

from app.config import settings


def init_stripe() -> None:
    """Initialize stripe.api_key from settings. Safe to call multiple times (idempotent)."""
    if settings.STRIPE_SECRET_KEY:
        _stripe.api_key = settings.STRIPE_SECRET_KEY


# Re-export stripe so callers import from here.
stripe = _stripe
