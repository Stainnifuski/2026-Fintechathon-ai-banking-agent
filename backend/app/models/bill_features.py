"""Versioned user decisions and forecast evidence; financial ledger stays immutable."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class BillClassificationORM(TimestampMixin, Base):
    __tablename__ = 'bill_classifications'
    tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), primary_key=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class BillCategoryRuleORM(TimestampMixin, Base):
    __tablename__ = 'bill_category_rules'
    merchant_id: Mapped[str] = mapped_column(ForeignKey('bill_merchants.merchant_id'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    category: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)


class BillBudgetORM(TimestampMixin, Base):
    __tablename__ = 'bill_budgets'
    budget_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('accounts.account_id'), nullable=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount_fen: Mapped[int] = mapped_column(BigInteger)
    warning_percent: Mapped[int] = mapped_column(Integer, default=80)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class BillAnomalyReviewORM(TimestampMixin, Base):
    __tablename__ = 'bill_anomaly_reviews'
    anomaly_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default='pending')
    note: Mapped[str] = mapped_column(String(256), default='')
    version: Mapped[int] = mapped_column(Integer, default=1)


class BillRecurringScheduleORM(Base):
    __tablename__ = 'bill_recurring_schedules'
    stream_id: Mapped[str] = mapped_column(ForeignKey('bill_recurring_streams.stream_id'), primary_key=True)
    anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category: Mapped[str] = mapped_column(String(20), default='other')


class BillForecastSnapshotORM(TimestampMixin, Base):
    __tablename__ = 'bill_forecast_snapshots'
    snapshot_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('accounts.account_id'), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opening_fen: Mapped[int] = mapped_column(BigInteger)
    predicted_inflow_fen: Mapped[int] = mapped_column(BigInteger)
    predicted_outflow_fen: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)


class MerchantRefundPartORM(TimestampMixin, Base):
    __tablename__ = 'merchant_refund_parts'
    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payment_request_id: Mapped[str] = mapped_column(ForeignKey('payment_requests.payment_request_id'), index=True)
    merchant_account_id: Mapped[str] = mapped_column(ForeignKey('accounts.account_id'))
    customer_account_id: Mapped[str] = mapped_column(ForeignKey('accounts.account_id'))
    amount_fen: Mapped[int] = mapped_column(BigInteger)
    merchant_tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), unique=True)
    customer_tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='SUCCEEDED')


class BillSyncStateORM(Base):
    __tablename__ = 'bill_sync_states'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
