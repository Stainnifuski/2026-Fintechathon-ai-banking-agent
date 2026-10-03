"""用户日历事件与导入的 iCalendar 系列。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CalendarEventORM(TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("user_id", "import_key", name="uq_calendar_user_import"),)

    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(500), default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    import_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ics_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_zone: Mapped[str] = mapped_column(String(100), default="UTC")
