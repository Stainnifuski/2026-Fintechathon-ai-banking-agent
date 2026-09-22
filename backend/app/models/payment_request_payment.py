"""收款申请与安全转账操作之间的银行侧绑定。"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class PaymentRequestPaymentORM(TimestampMixin, Base):
    __tablename__ = "payment_request_payments"

    payment_request_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("payment_requests.payment_request_id"), primary_key=True
    )
    operation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    payer_account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), nullable=False
    )
