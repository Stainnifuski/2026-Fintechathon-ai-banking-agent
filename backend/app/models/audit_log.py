"""审计日志数据模型"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuditLogORM(Base):
    """审计日志 ORM 模型。"""

    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="审计ID")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="会话ID")
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="用户ID")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, comment="时间戳"
    )
    intent: Mapped[str] = mapped_column(String(64), nullable=False, comment="识别到的意图")
    permission_level: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="权限级别: green|yellow|red"
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="执行的操作名")
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, comment="操作参数")
    user_confirmed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="用户是否确认"
    )
    mfa_method: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="MFA方式: sms|face|ukey"
    )
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="结果: success|failed|cancelled|blocked"
    )
    decision_chain: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="完整决策链路"
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="IP地址")
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备ID")


class AuditLogSchema(BaseModel):
    """审计日志响应 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    audit_id: str
    session_id: str
    user_id: str
    timestamp: datetime
    intent: str
    permission_level: str
    action: str
    params: dict
    user_confirmed: bool | None = None
    mfa_method: str | None = None
    result: str
    decision_chain: list[str]
    ip: str | None = None
    device_id: str | None = None


class AuditLogCreateSchema(BaseModel):
    """创建审计日志请求 Schema。"""

    session_id: str
    user_id: str
    intent: str
    permission_level: str
    action: str
    params: dict = {}
    user_confirmed: bool | None = None
    mfa_method: str | None = None
    result: str
    decision_chain: list[str] = []
    ip: str | None = None
    device_id: str | None = None
