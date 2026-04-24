"""Billing webhook handler registry.

Defines EVENT_HANDLERS dict and register() decorator only.
No handler imports here — handler modules import register back from this
package, so importing them here would create a circular-import mid-load.
Side-effect imports (e.g. `import app.billing.handlers.topup`) live in
routes/billing.py, co-located with the dispatch call.
"""

from typing import Awaitable, Callable

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

EventHandler = Callable[[stripe.Event, AsyncSession], Awaitable[None]]
EVENT_HANDLERS: dict[str, EventHandler] = {}


def register(event_type: str) -> Callable[[EventHandler], EventHandler]:
    """Decorator that registers a handler for a Stripe event type.

    Usage::

        @register("payment_intent.succeeded")
        async def handle_payment_intent_succeeded(event, db):
            ...
    """

    def decorator(fn: EventHandler) -> EventHandler:
        EVENT_HANDLERS[event_type] = fn
        return fn

    return decorator
