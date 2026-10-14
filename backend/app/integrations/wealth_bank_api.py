"""5号及页面服务访问4号理财能力的HTTP契约；禁止银行ORM依赖。"""

from .bank_api import HttpBankApiClient
from ..schemas.wealth import InvestmentCommand


class HttpWealthBankApi(HttpBankApiClient):
    """仅模拟银行连接器，没有真实资金切换开关。"""
    async def call(self, method: str, suffix: str, body: dict | None = None) -> dict | list:
        """统一保留超时/5xx未知语义，不自动换号或重试写入。"""
        response = await self._request(method, f"/api/v1/internal/bank/wealth{suffix}",
                                       **({"json": body} if body is not None else {}))
        if response.is_error:
            raise self._error(response)
        return response.json()

    async def products(self, user_id: str | None = None) -> list[dict]:
        """只读客观产品信息。"""
        return await self.call("POST", "/catalog", {"subject_user_id": user_id}) if user_id else await self.call("GET", "/products")

    async def assets(self, user_id: str) -> dict:
        """查询本人事实，外部资产不混入银行现金。"""
        return await self.call("POST", "/assets", {"subject_user_id": user_id})

    async def selected_products(self, user_id: str, product_ids: list[str]) -> list[dict]:
        """Fresh protected exact-code facts; [] means no products, never full pool."""
        return await self.call("POST", "/catalog", {"subject_user_id": user_id,
            "product_ids": sorted(set(product_ids)), "facts_only": True})

    async def precheck(self, user_id: str, command: InvestmentCommand) -> dict:
        """可信预览，不扣款。"""
        return await self.call("POST", "/precheck", {"subject_user_id": user_id, "command": command.model_dump()})

    async def submit(self, user_id: str, command: InvestmentCommand, authorization_id: str) -> dict:
        """原号提交，银行独立校验已验证授权。"""
        return await self.call("POST", "/orders", {"subject_user_id": user_id,
            "command": command.model_dump(), "authorization_id": authorization_id})

    async def query(self, user_id: str, operation_id: str) -> dict:
        """查询原操作号的银行事实。"""
        return await self.call("POST", f"/orders/{operation_id}/query", {"subject_user_id": user_id})
