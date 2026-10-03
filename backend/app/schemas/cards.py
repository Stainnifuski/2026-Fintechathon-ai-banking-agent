"""卡片管理公开 HTTP 契约；金额以元输入、以分返回。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from ..domain import yuan_to_fen
from ..domain.time import as_utc

CardAction = Literal[
    "card_apply_virtual", "card_set_transaction_limits", "card_adjust_credit_limit",
    "card_unlock", "card_report_lost",
]

# 每个动作必须且只能携带的字段；其余字段一律拒绝，避免带入未核对参数。
ACTION_FIELDS: dict[str, set[str]] = {
    "card_apply_virtual": {"account_id"},
    "card_set_transaction_limits": {"card_id", "single_txn_limit", "daily_txn_limit"},
    "card_adjust_credit_limit": {"card_id", "credit_limit"},
    "card_unlock": {"card_id"},
    "card_report_lost": {"card_id"},
}
OPTIONAL_FIELDS = {"card_id", "account_id", "credit_limit", "single_txn_limit", "daily_txn_limit"}


class CardOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=64)
    action: CardAction
    card_id: str | None = Field(default=None, max_length=32)
    account_id: str | None = Field(default=None, max_length=32)
    credit_limit: Decimal | None = None
    single_txn_limit: Decimal | None = None
    daily_txn_limit: Decimal | None = None

    @model_validator(mode="after")
    def fields_match_action(self) -> "CardOperationCreate":
        required = ACTION_FIELDS[self.action]
        provided = {name for name in OPTIONAL_FIELDS if getattr(self, name) is not None}
        if provided != required:
            raise ValueError(f"{self.action} 需要且只接受字段：{', '.join(sorted(required))}")
        for name in ("credit_limit", "single_txn_limit", "daily_txn_limit"):
            if getattr(self, name) is not None:
                yuan_to_fen(getattr(self, name))
        return self

    def bank_params(self) -> dict:
        """规范化为银行参数（分），供预览、摘要和受理共用。"""
        params: dict = {}
        if self.account_id is not None:
            params["account_id"] = self.account_id
        for name in ("credit_limit", "single_txn_limit", "daily_txn_limit"):
            if getattr(self, name) is not None:
                params[f"{name}_fen"] = yuan_to_fen(getattr(self, name))
        return params


class CardConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(min_length=64, max_length=64)


class CardVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proof: str = Field(pattern=r"^\d{6}$")


class CardView(BaseModel):
    card_id: str
    account_id: str
    account_label: str
    card_type: Literal["DEBIT", "CREDIT"]
    is_virtual: bool
    card_number: str
    status: Literal["ACTIVE", "LOCKED", "LOST"]
    lock_reason: str | None = None
    credit_limit_fen: int | None = None
    credit_limit_cap_fen: int | None = None
    single_txn_limit_fen: int
    daily_txn_limit_fen: int
    single_txn_cap_fen: int
    daily_txn_cap_fen: int
    state_version: int
    lost_reported_at: datetime | None = None

    @field_validator('lost_reported_at')
    @classmethod
    def utc_reported_at(cls, value: datetime | None) -> datetime | None:
        """银行事件按UTC传输，兼容SQLite读取的无时区UTC值。"""
        return as_utc(value, legacy_utc=True) if value is not None else None


class CardChange(BaseModel):
    field: str
    label: str
    before: int | str | None = None
    after: int | str | None = None
    unit: Literal["fen"] | None = None


class CardOperationView(BaseModel):
    operation_id: str
    action: CardAction
    status: str
    permission_level: Literal["green", "yellow", "red"]
    requires_confirmation: bool
    requires_mfa: bool
    digest: str
    card_id: str | None = None
    card: CardView | None = None
    changes: list[CardChange]
    result: CardView | None = None
    error_code: str | None = None
    failed_verifications: int
    expires_at: datetime
    allowed_actions: list[str]
    message: str

    @field_validator('expires_at')
    @classmethod
    def utc_expires_at(cls, value: datetime) -> datetime:
        """安全有效期保持现实瞬时，不随浏览器所在地变化。"""
        return as_utc(value, legacy_utc=True)
