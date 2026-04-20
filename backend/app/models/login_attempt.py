from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.base import Base


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        Index("ix_login_attempts_email_attempted_at", "email", "attempted_at"),
    )
