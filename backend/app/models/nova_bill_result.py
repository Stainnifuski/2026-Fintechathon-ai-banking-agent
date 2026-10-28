"""Encrypted, immutable snapshots of read-only bill queries."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class NovaBillResultORM(Base):
    __tablename__ = 'nova_bill_results'
    result_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('nova_conversations.conversation_id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    payload_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
