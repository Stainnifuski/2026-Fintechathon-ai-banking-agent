"""Append-only audit facts, independent of disposable task and conversation rows."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base


class AuditEventORM(Base):
    __tablename__ = "audit_events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    trace_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    span_id: Mapped[str] = mapped_column(String(64))
    parent_span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16))
    domain: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(128))
    phase: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    permission_level: Mapped[str] = mapped_column(String(16), default="unknown")
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    source_module: Mapped[str] = mapped_column(String(128))
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    business_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    objects: Mapped[list["AuditEventObjectORM"]] = relationship(back_populates="event", cascade="save-update, merge")
    __table_args__ = (
        Index("ix_audit_owner_time", "subject_user_id", "occurred_at", "event_id"),
        Index("ix_audit_owner_action", "subject_user_id", "domain", "action", "outcome"),
        Index("ix_audit_trace", "trace_id", "sequence"),
    )


class AuditEventObjectORM(Base):
    __tablename__ = "audit_event_objects"
    event_id: Mapped[str] = mapped_column(ForeignKey("audit_events.event_id"), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event: Mapped[AuditEventORM] = relationship(back_populates="objects")
    __table_args__ = (Index("ix_audit_object", "domain", "object_type", "object_id", "event_id"),)
