"""Bounded encrypted object references; never a source of money or authority."""
from sqlalchemy import ForeignKey,String,Text
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base


class NovaReferenceContextORM(Base):
    __tablename__='nova_reference_contexts'
    conversation_id:Mapped[str]=mapped_column(String(64),ForeignKey('nova_conversations.conversation_id',ondelete='CASCADE'),primary_key=True)
    user_id:Mapped[str]=mapped_column(String(32),nullable=False,index=True)
    payload_ciphertext:Mapped[str]=mapped_column(Text,nullable=False)
