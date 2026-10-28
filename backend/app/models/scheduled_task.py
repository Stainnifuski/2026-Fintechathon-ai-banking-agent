"""User-authored scheduled NOVA instructions; no automatic bank execution."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ScheduledTaskORM(TimestampMixin, Base):
    __tablename__ = "scheduled_custom_tasks"

    task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.user_id"), nullable=False, index=True)
    description_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="SCHEDULED")
    dispatch_request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScheduledTaskFundingORM(TimestampMixin, Base):
    """Cash locked for a custom task and the NOVA conversation that may spend it once started."""
    __tablename__ = "scheduled_task_funding"

    task_id: Mapped[str] = mapped_column(String(40), ForeignKey("scheduled_custom_tasks.task_id"), primary_key=True)
    hold_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class ScheduledTransferFundingORM(TimestampMixin, Base):
    """A transfer task created inside a running custom task's conversation, paid from its lock."""
    __tablename__ = "scheduled_transfer_funding"

    transfer_task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    custom_task_id: Mapped[str] = mapped_column(String(40), ForeignKey("scheduled_custom_tasks.task_id"), nullable=False)
