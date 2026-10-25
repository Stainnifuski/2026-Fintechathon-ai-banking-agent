"""Conversation-scoped NOVA task ledger, independent of transfer form versions."""
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class NovaAgentRuntimeORM(TimestampMixin, Base):
    __tablename__ = "nova_agent_runtime"

    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("nova_conversations.conversation_id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
