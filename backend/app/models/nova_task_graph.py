"""Encrypted task-graph snapshots planned by NOVA, one per request."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class NovaTaskGraphORM(Base):
    __tablename__ = 'nova_task_graphs'
    snapshot_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('nova_conversations.conversation_id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    graph_id: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer)
    payload_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
