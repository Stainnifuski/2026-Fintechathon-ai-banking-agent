"""B3d实际收益读取协议；录制样例与银行计算共用。"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReturnStatus = Literal['complete', 'partial', 'unavailable']
ReturnPeriod = Literal['day', 'month', 'year', 'cumulative']


class ReturnsQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['all', 'product'] = 'all'
    product_id: str | None = Field(default=None, min_length=1, max_length=120)
    period: ReturnPeriod = 'day'
    anchor_date: date | None = None
    overview: bool = False

    @model_validator(mode='after')
    def product_scope(self):
        if self.overview and (self.scope!='all' or self.product_id is not None or self.anchor_date is not None):
            raise ValueError('整体表现仅支持自动日期的全部产品范围')
        if (self.scope == 'product') != (self.product_id is not None):
            raise ValueError('单产品范围需要具体份额代码，整体范围不得附带产品代码')
        if self.period == 'cumulative' and self.anchor_date is not None:
            raise ValueError('累计从可核实覆盖起点计算，不接受任意起点')
        return self


class ReturnSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    period_pnl_fen: int | None = Field(strict=True)
    cumulative_pnl_fen: int | None = Field(strict=True)
    realized_pnl_fen: int | None = Field(strict=True)
    unrealized_pnl_fen: int | None = Field(strict=True)
    fees_fen: int | None = Field(strict=True, ge=0)


class ReturnPoint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    period_start: date
    period_end: date
    pnl_fen: int | None = Field(strict=True)
    status: ReturnStatus
    reason: str | None = None


class ProductReturn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str
    product_name: str
    period_pnl_fen: int | None = Field(strict=True)
    cumulative_pnl_fen: int | None = Field(strict=True)
    status: ReturnStatus
    reason: str | None = None


class PerformancePoint(BaseModel):
    model_config=ConfigDict(extra='forbid')
    date: date
    rate_percent: str | None = Field(pattern=r'^-?\d+(\.\d+)?$')


class PositionDailyReturn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    position_key: str
    yesterday_pnl_fen: int | None = Field(strict=True)


class PerformanceOverview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    period: ReturnPeriod
    start_date: date
    end_date: date
    amount_fen: int | None = Field(strict=True)
    rate_percent: str | None = Field(pattern=r'^-?\d+(\.\d+)?$')
    points: list[PerformancePoint]
    notice: str
    positions: list[PositionDailyReturn]
    rate_method: Literal['modified-dietz-nonannualized-v1']
    as_of: datetime


class WealthReturns(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['wealth-returns-v1'] = 'wealth-returns-v1'
    asset_scope_id: str
    snapshot_id: str
    scope: Literal['all', 'product']
    product_id: str | None
    period: ReturnPeriod
    start_date: date
    end_date: date
    as_of: datetime
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    coverage_start: date | None
    status: ReturnStatus
    missing_fields: list[str]
    summary: ReturnSummary
    points: list[ReturnPoint]
    products: list[ProductReturn]
    data_source: str
    valuation_as_of: date | None
    formula_version: str
    performance: PerformanceOverview | None = None

    @model_validator(mode='after')
    def coherent(self):
        if (self.scope == 'product') != (self.product_id is not None):
            raise ValueError('范围与份额代码不一致')
        if self.as_of.tzinfo is None or self.start_date > self.end_date:
            raise ValueError('收益时间需时区且起止有效')
        if self.status == 'complete' and self.missing_fields:
            raise ValueError('存在缺失事实不能称完整')
        ids = [row.product_id for row in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError('产品贡献按具体份额代码独立汇总')
        if self.scope == 'product' and ids != [self.product_id]:
            raise ValueError('单产品贡献必须与所选代码一致')
        for field in ('period_pnl_fen', 'cumulative_pnl_fen'):
            values = [getattr(row, field) for row in self.products]
            total = getattr(self.summary, field)
            if total is not None and (any(value is None for value in values) or sum(values) != total):
                raise ValueError('整体收益必须与全量产品贡献一致')
        if any(point.pnl_fen is None and point.status == 'complete' for point in self.points):
            raise ValueError('缺失收益点不能标完整')
        return self
