"""日历 HTTP 契约。时间点事件的 ends_at 为 null。"""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CalendarEventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    location: str = Field(default="", max_length=500)
    starts_at: datetime
    ends_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("事件名称不能为空")
        return value.strip()

    @field_validator("starts_at", "ends_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("时间必须包含时区")
            return value.astimezone(timezone.utc)
        return value

    @model_validator(mode="after")
    def valid_range(self):
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class CalendarEventView(BaseModel):
    event_id: str
    title: str
    description: str
    location: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool = False
    recurring: bool = False
    source: Literal["manual", "ics"]


class CalendarImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    total: int
