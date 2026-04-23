from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.base import Base


class BalanceLedger(Base):
    __tablename__ = "balance_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # Not a ForeignKey — Stripe event IDs are not our PKs; partial unique index
    # on this column is the webhook idempotency guarantee.
    stripe_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ref_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="balance_ledger_entries")  # noqa: F821

    __table_args__ = (
        # Compound index for balance queries by user, ordered by time.
        Index("ix_balance_ledger_user_id_created_at", "user_id", "created_at"),
        # Partial unique index — idempotency guarantee for webhook-driven grants.
        # Prevents two ledger rows from the same Stripe event id.
        # WHERE clause mirrors the Alembic migration exactly.
        Index(
            "ix_balance_ledger_stripe_event_id",
            "stripe_event_id",
            unique=True,
            postgresql_where=text("stripe_event_id IS NOT NULL"),
        ),
    )
