"""短期方案store；副本读取和原子CAS，不写银行数据库。"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Callable
from ...schemas.wealth_portfolio import PortfolioPlan
from .wealth_portfolio_candidates import CandidateError

@dataclass
class PortfolioRecord:
    user_id: str
    access: dict
    fingerprint: str
    plan: PortfolioPlan
    metadata: dict
    explicit: list

class PortfolioStore(Protocol):
    async def insert(self, record: PortfolioRecord) -> None: ...
    async def read(self, portfolio_id: str, user_id: str) -> PortfolioRecord: ...
    async def replace(self, record: PortfolioRecord, expected_version: int) -> None: ...

class MemoryPortfolioStore:
    """单进程30分钟空闲清理；touch不能延长评估/事实有效期。"""
    def __init__(self, clock: Callable[[],datetime], idle_seconds: int=1800):
        self.clock=clock;self.idle_seconds=idle_seconds;self.rows={};self.lock=asyncio.Lock()
    def _row(self, key: str, user: str) -> PortfolioRecord:
        item=self.rows.get(key)
        if item and (self.clock()-item[1]).total_seconds()>=self.idle_seconds:
            self.rows.pop(key);item=None
        if not item or item[0].user_id!=user:raise CandidateError('PORTFOLIO_NOT_FOUND',404)
        return item[0]
    async def insert(self, record: PortfolioRecord) -> None:
        async with self.lock:
            now=self.clock()
            for key,(_,touch) in list(self.rows.items()):
                if (now-touch).total_seconds()>=self.idle_seconds:self.rows.pop(key)
            if record.plan.portfolio_id in self.rows:raise CandidateError('PORTFOLIO_ID_CONFLICT')
            self.rows[record.plan.portfolio_id]=(deepcopy(record),now)
    async def read(self, portfolio_id: str, user_id: str) -> PortfolioRecord:
        async with self.lock:
            row=self._row(portfolio_id,user_id);self.rows[portfolio_id]=(row,self.clock());return deepcopy(row)
    async def replace(self, record: PortfolioRecord, expected_version: int) -> None:
        async with self.lock:
            prior=self._row(record.plan.portfolio_id,record.user_id)
            if prior.plan.version!=expected_version:raise CandidateError('PORTFOLIO_VERSION_STALE')
            if record.plan.version!=expected_version+1:raise CandidateError('PORTFOLIO_VERSION_INVALID')
            self.rows[record.plan.portfolio_id]=(deepcopy(record),self.clock())
