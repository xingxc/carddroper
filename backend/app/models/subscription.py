from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stripe_subscription_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    stripe_price_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tier_name: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # mirrored from Price.metadata.tier_name at subscribe + on update
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    grant_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_period_start: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="subscription")  # noqa: F821
