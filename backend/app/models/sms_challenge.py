"""短信验证码挑战：每条验证码绑定一个用户与一笔具体业务操作。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SmsChallengeORM(Base):
    """短信第二因素挑战；只保存验证码哈希，成功一次即消费。"""

    __tablename__ = "sms_challenges"

    challenge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, comment="transfer|card|wealth")
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务/卡片操作/理财授权ID")
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
