"""Persistent security counters, separate from customer/card business status."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SecurityStateORM(Base):
    __tablename__ = "security_states"

    subject_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    counters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
