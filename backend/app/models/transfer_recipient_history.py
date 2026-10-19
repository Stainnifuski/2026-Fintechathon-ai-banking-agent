"""Per-user cutoff for the recipient shortcut history; ledger rows stay intact."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TransferRecipientHistoryCutoffORM(Base):
    __tablename__ = "transfer_recipient_history_cutoffs"

    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.user_id"), primary_key=True)
    cleared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
