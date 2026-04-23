from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.base import Base


class StripeEvent(Base):
    """Records every processed Stripe webhook event id.

    Provides idempotency: before processing any event, check if its id is
    already here. Stripe may deliver the same event multiple times.
    """

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
