"""账户数据模型"""

from pydantic import BaseModel, ConfigDict
from decimal import Decimal
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from ..domain.money import exact_fen, decimal_yuan


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
    balance_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("balance_fen >= 0 AND balance_fen = CAST(balance_fen AS BIGINT)"), nullable=False, default=0)
    daily_transfer_used_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("daily_transfer_used_fen >= 0 AND daily_transfer_used_fen = CAST(daily_transfer_used_fen AS BIGINT)"), nullable=False, default=0)
    daily_transfer_limit_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("daily_transfer_limit_fen >= 0 AND daily_transfer_limit_fen = CAST(daily_transfer_limit_fen AS BIGINT)"), nullable=False, default=5_000_000)

    @property
    def balance(self) -> Decimal:
        return decimal_yuan(self.balance_fen)

    @balance.setter
    def balance(self, value: Decimal | float | int | str) -> None:
        self.balance_fen = exact_fen(value)

    @property
    def daily_transfer_used(self) -> Decimal:
        return decimal_yuan(self.daily_transfer_used_fen)

    @daily_transfer_used.setter
    def daily_transfer_used(self, value: Decimal | float | int | str) -> None:
        self.daily_transfer_used_fen = exact_fen(value)

    @property
    def daily_transfer_limit(self) -> Decimal:
        return decimal_yuan(self.daily_transfer_limit_fen)

    @daily_transfer_limit.setter
    def daily_transfer_limit(self, value: Decimal | float | int | str) -> None:
        self.daily_transfer_limit_fen = exact_fen(value)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="账户状态"
    )
    user: Mapped["UserORM"] = relationship(back_populates="accounts")
    transactions: Mapped[list["TransactionORM"]] = relationship(back_populates="account")


class AccountSchema(BaseModel):
    """账户信息响应 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    account_id: str
    user_id: str
    account_type: str
    card_number: str
    transfer_card_number: str | None = None
    balance: float
    available_fen: int | None = None
    held_fen: int | None = None
    daily_transfer_used: float
    daily_transfer_limit: float
    status: str


class AccountBalanceSchema(BaseModel):
    """账户余额查询响应 Schema。"""

    account_id: str
    account_type: str
    balance: float
    available_fen: int | None = None
    held_fen: int | None = None
    card_number: str
    daily_transfer_used: float
    daily_transfer_limit: float


class AccountCreateSchema(BaseModel):
    """注册时创建的基础活期账户。"""

    account_id: str
    account_type: str
    card_number: str
