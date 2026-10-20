"""Persisted task index for the unified NOVA conversation."""
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class NovaSessionORM(TimestampMixin, Base):
    __tablename__ = "nova_task_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class NovaMessageReceiptORM(Base):
    __tablename__ = "nova_task_message_receipts"

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)


class NovaTaskMessageORM(Base):
    __tablename__ = "nova_task_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("nova_task_sessions.session_id"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content_masked: Mapped[str] = mapped_column(Text, nullable=False)
    chat_task_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
