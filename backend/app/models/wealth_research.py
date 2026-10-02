"""版本化研究证据与沙箱归属；银行账户仍由4号持有。"""
from datetime import datetime
from sqlalchemy import String, Integer, JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class WealthDatasetORM(Base):
    __tablename__ = "wealth_datasets"
    dataset_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WealthResearchProductORM(Base):
    __tablename__ = "wealth_research_products"
    __table_args__ = (UniqueConstraint("dataset_key", "product_id"),)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dataset_key: Mapped[str] = mapped_column(ForeignKey("wealth_datasets.dataset_key"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    history: Mapped[list] = mapped_column(JSON, nullable=False)


class WealthContextORM(Base):
    __tablename__ = "wealth_contexts"
    context_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), nullable=False, unique=True)
    dataset_key: Mapped[str] = mapped_column(ForeignKey("wealth_datasets.dataset_key"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class WealthContextSelectionORM(Base):
    __tablename__ = "wealth_context_selections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    context_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WealthSyncJobORM(Base):
    __tablename__ = "wealth_sync_jobs"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WealthKnowledgeORM(Base):
    __tablename__ = "wealth_knowledge"
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    content: Mapped[str] = mapped_column(String(12000), nullable=False)


class WealthMacroORM(Base):
    __tablename__ = "wealth_macro"
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dataset_key: Mapped[str] = mapped_column(ForeignKey("wealth_datasets.dataset_key"), nullable=False, index=True)
    indicator: Mapped[str] = mapped_column(String(32), nullable=False)
    observations: Mapped[list] = mapped_column(JSON, nullable=False)


class WealthChatRequestORM(Base):
    """Client-message atomic claim and replay, without credentials/reasoning."""
    __tablename__ = 'wealth_chat_requests'
    request_key: Mapped[str] = mapped_column(String(96),primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32),nullable=False,index=True)
    digest: Mapped[str] = mapped_column(String(64),nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    response: Mapped[dict] = mapped_column(JSON,nullable=False,default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
