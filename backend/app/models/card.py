"""4号模拟银行持有的卡片事实与应用侧卡片操作授权记录。"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CardORM(TimestampMixin, Base):
    """银行卡片；状态、额度与上限只由模拟银行写入。"""

    __tablename__ = "cards"

    card_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), nullable=False
    )
    card_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="DEBIT|CREDIT")
    is_virtual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    card_number: Mapped[str] = mapped_column(String(32), nullable=False, comment="卡号(脱敏)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE",
                                        comment="ACTIVE|LOCKED|LOST")
    lock_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credit_limit_fen: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credit_limit_cap_fen: Mapped[int | None] = mapped_column(BigInteger, nullable=True,
                                                             comment="银行核定的最高信用额度")
    single_txn_limit_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    daily_txn_limit_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    single_txn_cap_fen: Mapped[int] = mapped_column(BigInteger, nullable=False,
                                                    comment="银行允许的单笔限额上限")
    daily_txn_cap_fen: Mapped[int] = mapped_column(BigInteger, nullable=False,
                                                   comment="银行允许的单日限额上限")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True,
                                                            comment="申请该卡的操作号")
    lost_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CardOperationORM(TimestampMixin, Base):
    """一次卡片操作的预览、本人确认与强验证；不持有卡片事实副本以外的银行数据。"""

    __tablename__ = "card_operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    card_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preview: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_level: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    failed_verifications: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
