"""用户数据模型"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class UserORM(TimestampMixin, Base):
    """用户 ORM 模型。"""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="用户ID")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="姓名")
    phone: Mapped[str] = mapped_column(String(20), nullable=False, comment="手机号(脱敏)")
    id_card: Mapped[str] = mapped_column(String(32), nullable=False, comment="身份证号(脱敏)")
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="balanced", comment="风险等级"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="账户状态"
    )


class UserSchema(BaseModel):
    """用户信息响应 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: str
    name: str
    phone: str
    id_card: str
    risk_level: str
    created_at: datetime
    status: str


class UserCreateSchema(BaseModel):
    """创建用户请求 Schema。"""

    user_id: str
    name: str
    phone: str
    id_card: str
    risk_level: str = "balanced"


class UserUpdateSchema(BaseModel):
    """更新用户请求 Schema。"""

    name: str | None = None
    phone: str | None = None
    risk_level: str | None = None
    status: str | None = None
