"""Consumer boundary for A's canonical portfolio service; no allocation algorithm.

Install a server-owned adapter in app.state.wealth_portfolio_source after A merges.
Tests inject recorded outputs via the same dependency. Clients cannot install a
provider, supply a plan, choose their identity, or enable a fixture bypass.
"""
from typing import Protocol
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...integrations.wealth_authorization import WealthPolicyError


class PortfolioSource(Protocol):
    async def owned_plan(self, db: AsyncSession, user_id: str, portfolio_id: str) -> dict:
        """Read A's current validated plan, checking ownership before returning it."""
        ...

    async def snapshot(self, db: AsyncSession, user_id: str, context_id: str,
                       bank: HttpWealthBankApi) -> dict:
        """Re-read A's canonical, authorized AssetSnapshot from bank facts."""
        ...


def get_portfolio_source(request: Request) -> PortfolioSource:
    source = getattr(request.app.state, 'wealth_portfolio_source', None)
    if source is None:
        raise WealthPolicyError('整体配置服务尚未接入；请使用开发样例核对，或手选现有模拟产品',
                               'PORTFOLIO_PROVIDER_UNAVAILABLE', 503)
    return source
