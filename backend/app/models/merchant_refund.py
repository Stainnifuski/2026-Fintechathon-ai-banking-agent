"""银行持有的商户退款事实。"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class MerchantRefundORM(TimestampMixin, Base):
    """一笔已付款收款申请最多对应一笔全额退款。"""

    __tablename__ = "merchant_refunds"

    payment_request_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("payment_requests.payment_request_id"),
        primary_key=True,
    )
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    merchant_account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), nullable=False
    )
    customer_account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), nullable=False
    )
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    merchant_tx_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    customer_tx_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCEEDED")
