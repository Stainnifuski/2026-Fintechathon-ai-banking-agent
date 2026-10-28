"""Bank-owned cash locks for scheduled work; locked cash is excluded from available funds."""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ScheduledCashHoldORM(TimestampMixin, Base):
    __tablename__ = "scheduled_cash_holds"
    __table_args__ = (CheckConstraint("used_fen >= 0 AND used_fen <= amount_fen"),)

    hold_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.user_id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.account_id"), nullable=False, index=True)
    amount_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("amount_fen > 0"), nullable=False)
    used_fen: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # HELD while the task is open; CONSUMED/RELEASED return any remainder to available cash.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="HELD", index=True)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScheduledCashHoldLinkORM(Base):
    """A bank operation or payment request that may draw on a lock."""
    __tablename__ = "scheduled_cash_hold_links"

    link_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    hold_id: Mapped[str] = mapped_column(String(64), ForeignKey("scheduled_cash_holds.hold_id"), nullable=False, index=True)
