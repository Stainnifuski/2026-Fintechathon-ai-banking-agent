"""蛋糕店订单持久化模型。"""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CakeOrderORM(TimestampMixin, Base):
    """蛋糕店领域拥有的订单；银行收款申请仍是付款事实来源。"""

    __tablename__ = "cake_orders"
    __table_args__ = (
        UniqueConstraint(
            "customer_user_id",
            "idempotency_key",
            name="uq_cake_order_customer_idempotency",
        ),
    )

    order_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(String(32), nullable=False)
    product_name: Mapped[str] = mapped_column(String(80), nullable=False)
    product_description: Mapped[str] = mapped_column(String(256), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    total_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    payment_request_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("payment_requests.payment_request_id"), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING_PAYMENT")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
