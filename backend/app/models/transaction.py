"""交易数据模型"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TransactionORM(Base):
    """交易记录 ORM 模型。"""

    __tablename__ = "transactions"

    tx_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="交易ID")
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), nullable=False, comment="账户ID"
    )
    type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="交易类型: transfer_in|transfer_out|purchase|refund|subscription",
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False, comment="金额")
    counterparty: Mapped[str] = mapped_column(String(64), nullable=False, comment="交易对手")
    category: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="分类: dining|shopping|transport|entertainment|transfer|subscription|salary|other",
    )
    note: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="备注")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, comment="交易时间"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="状态: completed|pending|failed|cancelled"
    )
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, default="agent", comment="渠道: agent|app|atm|pos"
    )


class TransactionSchema(BaseModel):
    """交易记录响应 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    tx_id: str
    account_id: str
    type: str
    amount: float
    counterparty: str
    category: str
    note: str | None = None
    timestamp: datetime
    status: str
    channel: str


class TransferRequestSchema(BaseModel):
    """转账请求 Schema。"""

    from_account: str
    to_contact: str
    amount: float = Field(..., gt=0, description="转账金额，必须大于0")
    note: str | None = None
    scheduled_time: str | None = None


class TransferResponseSchema(BaseModel):
    """转账响应 Schema。"""

    tx_id: str
    status: str
    amount: float
    from_account: str
    to_contact: str
    message: str
    timestamp: datetime
