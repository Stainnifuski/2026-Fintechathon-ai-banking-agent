"""应用对话状态，与银行账户表无外键/读取依赖。"""
from sqlalchemy import Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ConversationORM(Base):
    __tablename__ = "transfer_conversations"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class ChatReceiptORM(Base):
    __tablename__ = "transfer_chat_receipts"
    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
