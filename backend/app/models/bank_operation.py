"""4号模拟银行持有的幂等操作记录。"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class BankOperationORM(TimestampMixin, Base):
    """银行受理事实；5号只能通过模拟银行 API 获取其投影。"""

    __tablename__ = "bank_operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    from_account: Mapped[str] = mapped_column(String(32), nullable=False)
    to_account: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    bank_tx_id: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
