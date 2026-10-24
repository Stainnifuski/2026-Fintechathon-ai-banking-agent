"""Encrypted conversation-scoped execution queue, separate from bank orders."""
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class NovaExecutionORM(Base):
    __tablename__ = 'nova_execution_queues'
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey('nova_conversations.conversation_id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
