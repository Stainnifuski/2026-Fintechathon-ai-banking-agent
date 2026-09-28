"""批量收款根幂等键与完整请求绑定，防止重试增加参与人。"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class PaymentRequestBatchORM(TimestampMixin, Base):
    __tablename__ = "payment_request_batches"
    __table_args__ = (UniqueConstraint("requester_user_id", "idempotency_key"),)

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requester_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
