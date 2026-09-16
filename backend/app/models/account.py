"""账户数据模型"""

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class AccountORM(TimestampMixin, Base):
    """账户 ORM 模型。"""

    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="账户ID")
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, comment="用户ID"
    )
    account_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="账户类型: savings|checking|credit"
    )
    card_number: Mapped[str] = mapped_column(String(32), nullable=False, comment="卡号(脱敏)")
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, comment="余额")
    daily_transfer_used: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="今日已转金额"
    )
    daily_transfer_limit: Mapped[float] = mapped_column(
        Float, nullable=False, default=50000.0, comment="日转账限额"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="账户状态"
    )


class AccountSchema(BaseModel):
    """账户信息响应 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    account_id: str
    user_id: str
    account_type: str
    card_number: str
    balance: float
    daily_transfer_used: float
    daily_transfer_limit: float
    status: str


class AccountBalanceSchema(BaseModel):
    """账户余额查询响应 Schema。"""

    account_id: str
    account_type: str
    balance: float
    card_number: str
    daily_transfer_used: float
    daily_transfer_limit: float
