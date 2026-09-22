"""银行拥有的收款申请持久化模型。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class PaymentRequestORM(TimestampMixin, Base):
    """用户向另一位本行客户发出的收款申请。

    该表属于模拟银行域，只能由 ``services.bank`` 中的实现访问。
    """

    __tablename__ = "payment_requests"
    __table_args__ = (
        UniqueConstraint(
            "requester_user_id",
            "idempotency_key",
            name="uq_payment_request_requester_idempotency",
        ),
    )

    payment_request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    requester_user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, index=True
    )
    payer_user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, index=True
    )
    receive_account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), nullable=False
    )
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
