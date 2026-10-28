"""User-authored scheduled NOVA instructions; no automatic bank execution."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
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
