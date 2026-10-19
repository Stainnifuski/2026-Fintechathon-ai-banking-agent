"""Additive bill metadata. Original financial transactions remain immutable."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class BillFoundationLockORM(Base):
    __tablename__ = 'bill_foundation_locks'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)


class BillMerchantORM(TimestampMixin, Base):
    __tablename__ = 'bill_merchants'
    merchant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    bank_identity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey('bill_merchants.merchant_id'), nullable=True)


class BillMerchantAliasORM(Base):
    __tablename__ = 'bill_merchant_aliases'
    __table_args__ = (UniqueConstraint('user_id', 'normalized_name'),)
    alias_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    name: Mapped[str] = mapped_column(String(64))
    normalized_name: Mapped[str] = mapped_column(String(256))
    merchant_id: Mapped[str] = mapped_column(ForeignKey('bill_merchants.merchant_id'), index=True)


class BillTransferPairORM(TimestampMixin, Base):
    __tablename__ = 'bill_transfer_pairs'
    pair_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    operation_id: Mapped[str] = mapped_column(String(64), unique=True)
    debit_tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), unique=True)
    credit_tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), unique=True)
    source: Mapped[str] = mapped_column(String(24), default='bank_operation')


class BillRecurringStreamORM(TimestampMixin, Base):
    __tablename__ = 'bill_recurring_streams'
    stream_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.account_id'), index=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey('bill_merchants.merchant_id'))
    kind: Mapped[str] = mapped_column(String(16))  # income / expense
    source: Mapped[str] = mapped_column(String(24))  # subscription / detected
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey('subscriptions.subscription_id'), nullable=True, unique=True)
    frequency: Mapped[str] = mapped_column(String(16), default='MONTHLY')
    anchor_day: Mapped[int] = mapped_column(Integer)
    month_end: Mapped[bool] = mapped_column(Boolean, default=False)
    amount_fen: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default='ACTIVE')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillRecurringOccurrenceORM(Base):
    __tablename__ = 'bill_recurring_occurrences'
    stream_id: Mapped[str] = mapped_column(ForeignKey('bill_recurring_streams.stream_id'), primary_key=True)
    tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), primary_key=True)


class BillTransactionMetaORM(TimestampMixin, Base):
    __tablename__ = 'bill_transaction_metadata'
    tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), primary_key=True)
    merchant_id: Mapped[str | None] = mapped_column(ForeignKey('bill_merchants.merchant_id'), nullable=True, index=True)
    original_tx_id: Mapped[str | None] = mapped_column(ForeignKey('transactions.tx_id'), nullable=True, index=True)
    refund_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    transfer_pair_id: Mapped[str | None] = mapped_column(ForeignKey('bill_transfer_pairs.pair_id'), nullable=True, index=True)
    recurring_stream_id: Mapped[str | None] = mapped_column(ForeignKey('bill_recurring_streams.stream_id'), nullable=True, index=True)
    annotation_version: Mapped[int] = mapped_column(Integer, default=0)


class BillUserTagORM(TimestampMixin, Base):
    __tablename__ = 'bill_user_tags'
    __table_args__ = (UniqueConstraint('user_id', 'normalized_name'),)
    tag_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    name: Mapped[str] = mapped_column(String(24))
    normalized_name: Mapped[str] = mapped_column(String(128))


class BillTransactionTagORM(Base):
    __tablename__ = 'bill_transaction_tags'
    tx_id: Mapped[str] = mapped_column(ForeignKey('transactions.tx_id'), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey('bill_user_tags.tag_id'), primary_key=True)


class BillMetadataEventORM(TimestampMixin, Base):
    __tablename__ = 'bill_metadata_events'
    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    action: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
