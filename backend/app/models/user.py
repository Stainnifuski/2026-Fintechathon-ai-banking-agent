"""用户数据模型"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class UserORM(TimestampMixin, Base):
    """用户 ORM 模型。"""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="用户ID")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="姓名")
    phone: Mapped[str] = mapped_column(String(20), nullable=False, comment="手机号(脱敏)")
    id_card: Mapped[str] = mapped_column(String(32), nullable=False, comment="身份证号(脱敏)")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False, default="", comment="密码哈希")
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="balanced", comment="风险等级"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="账户状态"
    )
    # TOTP（Google Authenticator）多因素验证。secret 为 base32 共享密钥；
    # totp_enabled 表示已完成绑定确认；totp_last_timestep 用于防重放。
    # 注意：生产环境 secret 应加密存储，此处为演示明文保存。
    totp_secret: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="TOTP 共享密钥(base32)"
    )
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否已完成 TOTP 绑定"
    )
    totp_last_timestep: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="最近成功验证的时间步(防重放)"
    )
    accounts: Mapped[list["AccountORM"]] = relationship(back_populates="user")
    contacts: Mapped[list["ContactORM"]] = relationship(back_populates="user")


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

    name: str
    phone: str
    password: str
    risk_level: str = "balanced"


class LoginSchema(BaseModel):
    """登录请求。"""

    phone: str
    password: str


class TokenSchema(BaseModel):
    """登录成功后的令牌及用户信息。"""

    access_token: str
    token_type: str = "bearer"
    user: UserSchema


class UserUpdateSchema(BaseModel):
    """更新用户请求 Schema。"""

    name: str | None = None
    phone: str | None = None
    risk_level: str | None = None
    status: str | None = None


class TotpStatusSchema(BaseModel):
    """TOTP 绑定状态。"""

    enabled: bool


class TotpEnrollSchema(BaseModel):
    """开始绑定时返回的扫码信息。"""

    secret: str
    otpauth_uri: str
    qr_data_uri: str


class TotpConfirmRequest(BaseModel):
    """确认绑定请求。"""

    code: str = Field(min_length=6, max_length=6)


class SmsSendRequest(BaseModel):
    """为一笔待强验证操作获取短信验证码。"""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["transfer", "card", "wealth"]
    reference_id: str = Field(min_length=1, max_length=64, description="任务ID/卡片操作ID/理财授权ID")


class SmsChallengeSchema(BaseModel):
    """已发送的短信挑战；不包含验证码本身。"""

    challenge_id: str
    phone_masked: str
    expires_in: int
    resend_after: int


class SmsInboxMessage(BaseModel):
    content: str
    sent_at: str
