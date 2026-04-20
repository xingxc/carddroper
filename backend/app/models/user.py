from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    verified_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    token_version: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    refresh_tokens: Mapped[List["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
