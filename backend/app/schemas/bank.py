"""银行响应与错误契约，供OpenAPI及调用方生成类型。"""

from typing import Literal
from pydantic import BaseModel, Field
from ..domain import BankOperationStatus


class BankOperationResultView(BaseModel):
    operation_id: str
    status: BankOperationStatus
    bank_tx_id: str | None = None
    error_code: str | None = None


class PayerCandidate(BaseModel):
    account_id: str
    label: str


class RecipientCandidate(PayerCandidate):
    recipient_user_id: str
    phone_masked: str | None = None


class TransferDirectoryView(BaseModel):
    payers: list[PayerCandidate]
    default_payer: str | None
    recipients: list[RecipientCandidate]


class PhoneRecipientView(PayerCandidate):
    name: str | None = None
    card_number: str | None = None
    phone_number: str | None = None
    preferred_mode: str | None = None
    card_account_id: str | None = None
    phone_account_id: str | None = None


class RecipientView(BaseModel):
    user_id: str
    contact_id: str | None = None
    name: str
    nickname: str | None = None
    phone: str | None = Field(default=None, description="仅在用户已提供手机号时返回脱敏号码")
    card_masked: str | None = None
    account_id: str
    bank: str


class TotpVerificationView(BaseModel):
    verified: Literal[True]


class ValidationIssue(BaseModel):
    loc: list[str | int]
    type: str
    message: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    issues: list[ValidationIssue] = Field(default_factory=list)


class BankErrorView(BaseModel):
    detail: ErrorDetail


BANK_ERROR_RESPONSES = {
    status: {"model": BankErrorView, "description": description}
    for status, description in {
        400: "银行明确拒绝", 401: "未认证", 403: "无权访问", 404: "未找到",
        409: "幂等、映射或版本冲突", 422: "输入无效", 503: "服务暂不可用，写操作结果需核对",
        504: "响应丢失，必须按原操作编号查询",
    }.items()
}
