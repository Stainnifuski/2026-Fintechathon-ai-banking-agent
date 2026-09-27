"""5号调用4号模拟银行时使用的 HTTP 客户端契约。

本模块不能导入任何银行 ORM 或数据库会话。任务编排只依赖这里定义的
数据传输对象和接口，银行账本的读取、校验与写入全部留在模拟银行服务内。
"""

import os
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx


class BankApiError(Exception):
    def __init__(self, message: str, code: str = "BANK_API_ERROR", status_code: int = 502) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class BankResponseUnknown(BankApiError):
    """银行可能已受理，但调用方没有得到可信响应。"""


@dataclass(frozen=True)
class BankTransferCommand:
    operation_id: str
    from_account: str
    to_account: str
    amount_fen: int
    currency: str
    param_digest: str
    note: str | None = None
    recipient_phone: str | None = None


@dataclass(frozen=True)
class TrustedExecutionContext:
    subject_user_id: str
    authorization_id: str
    control_epoch: int


@dataclass(frozen=True)
class BankOperationResult:
    operation_id: str
    status: str
    bank_tx_id: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class TransferValidation:
    from_account: str
    to_account: str
    recipient_name: str
    daily_transfer_used_fen: int


@dataclass(frozen=True)
class ResolvedTransferParties(TransferValidation):
    recipient_user_id: str


class BankApi(Protocol):
    async def transfer_directory(self, *, subject_user_id: str, recipient_name: str | None = None, recipient_phone: str | None = None) -> dict: ...

    async def validate_transfer(
        self, *, subject_user_id: str, from_account: str, to_account: str,
        recipient_phone: str | None = None,
    ) -> TransferValidation: ...

    async def resolve_transfer_parties(
        self, *, subject_user_id: str, recipient_name: str
    ) -> ResolvedTransferParties: ...

    async def submit_transfer(
        self,
        command: BankTransferCommand,
        context: TrustedExecutionContext,
        *,
        simulate_response_loss: bool = False,
    ) -> BankOperationResult: ...

    async def query_operation(self, operation_id: str) -> BankOperationResult | None: ...

    async def release_payment_request(
        self, *, operation_id: str, subject_user_id: str
    ) -> None: ...


class HttpBankApiClient:
    """通过模拟银行内部 API 访问银行能力，不接触银行数据库。"""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("BANK_API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token or os.getenv("INTERNAL_BANK_TOKEN")
        if not self.token or self.token in {
            "dev-internal-token", "replace-with-a-separate-internal-secret"
        }:
            raise BankApiError("内部银行凭据未安全配置", "INTERNAL_BANK_TOKEN_MISSING", 503)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Token": self.token}

    @staticmethod
    def _error(response: httpx.Response) -> BankApiError:
        try:
            detail = response.json().get("detail", {})
        except ValueError:
            detail = {}
        if isinstance(detail, str):
            return BankApiError(detail, status_code=response.status_code)
        return BankApiError(
            detail.get("message", "模拟银行接口调用失败"),
            detail.get("code", "BANK_API_ERROR"),
            response.status_code,
        )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
                response = await client.request(method, path, headers=self._headers, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise BankResponseUnknown("无法确认模拟银行是否已处理请求", "BANK_UNREACHABLE") from error
        if response.status_code >= 500:
            raise BankResponseUnknown("模拟银行响应异常，业务结果未知", "BANK_RESPONSE_UNKNOWN")
        return response

    async def validate_transfer(
        self, *, subject_user_id: str, from_account: str, to_account: str,
        recipient_phone: str | None = None,
    ) -> TransferValidation:
        response = await self._request(
            "POST",
            "/api/v1/internal/bank/transfers/validate",
            json={
                "subject_user_id": subject_user_id,
                "from_account": from_account,
                "to_account": to_account,
                "recipient_phone": recipient_phone,
            },
        )
        if response.is_error:
            raise self._error(response)
        return TransferValidation(**response.json())

    async def resolve_transfer_parties(
        self, *, subject_user_id: str, recipient_name: str
    ) -> ResolvedTransferParties:
        response = await self._request(
            "POST",
            "/api/v1/internal/bank/transfers/resolve",
            json={"subject_user_id": subject_user_id, "recipient_name": recipient_name},
        )
        if response.is_error:
            raise self._error(response)
        return ResolvedTransferParties(**response.json())

    async def transfer_directory(self, *, subject_user_id: str, recipient_name: str | None = None, recipient_phone: str | None = None) -> dict:
        response = await self._request("POST", "/api/v1/internal/bank/transfers/directory",
            json={"subject_user_id": subject_user_id, "recipient_name": recipient_name,
                  "recipient_phone": recipient_phone})
        if response.is_error:
            raise self._error(response)
        return response.json()

    async def submit_transfer(
        self,
        command: BankTransferCommand,
        context: TrustedExecutionContext,
        *,
        simulate_response_loss: bool = False,
    ) -> BankOperationResult:
        payload = asdict(command) | asdict(context) | {
            "simulate_response_loss": simulate_response_loss
        }
        response = await self._request(
            "POST", "/api/v1/internal/bank/operations", json=payload
        )
        if response.is_error:
            raise self._error(response)
        return BankOperationResult(**response.json())

    async def query_operation(self, operation_id: str) -> BankOperationResult | None:
        response = await self._request(
            "GET", f"/api/v1/internal/bank/operations/{operation_id}"
        )
        if response.status_code == 404:
            return None
        if response.is_error:
            raise self._error(response)
        return BankOperationResult(**response.json())

    async def release_payment_request(
        self, *, operation_id: str, subject_user_id: str
    ) -> None:
        response = await self._request(
            "POST",
            f"/api/v1/internal/bank/payment-requests/{operation_id}/release",
            json={"subject_user_id": subject_user_id},
        )
        if response.is_error:
            raise self._error(response)


def get_bank_api() -> BankApi:
    return HttpBankApiClient()
