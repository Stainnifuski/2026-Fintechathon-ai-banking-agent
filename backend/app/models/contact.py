"""联系人数据模型"""

from datetime import date

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ContactORM(Base):
    """联系人 ORM 模型。"""

    __tablename__ = "contacts"

    contact_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="联系人ID")
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id"), nullable=False, comment="用户ID"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="姓名")
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="昵称")
    phone: Mapped[str] = mapped_column(String(20), nullable=False, comment="手机号")
    account_number: Mapped[str] = mapped_column(String(32), nullable=False, comment="银行账号")
    bank: Mapped[str] = mapped_column(String(64), nullable=False, comment="银行名称")
    transfer_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="历史转账次数"
    )
    last_transfer: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="最近转账日期"
    )


class ContactSchema(BaseModel):
    """联系人信息响应 Schema。"""

    model_config = ConfigDict(from_attributes=True)

    contact_id: str
    user_id: str
    name: str
    nickname: str | None = None
    phone: str
    account_number: str
    bank: str
    transfer_count: int
    last_transfer: date | None = None
