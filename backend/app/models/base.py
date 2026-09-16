"""数据库模型基类"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类，所有 ORM 模型继承此类。"""
    pass


class TimestampMixin:
    """通用时间戳混入类，自动管理 created_at 和 updated_at 字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应 Schema。"""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
