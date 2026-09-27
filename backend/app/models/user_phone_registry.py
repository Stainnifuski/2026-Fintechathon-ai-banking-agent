"""规范化手机号的唯一性登记，用于升级旧库及并发注册。"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UserPhoneRegistryORM(Base):
    __tablename__ = "user_phone_registry"

    canonical_phone: Mapped[str] = mapped_column(String(11), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, unique=True
    )