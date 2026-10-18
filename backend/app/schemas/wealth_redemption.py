"""B4 manual input; exactly one expression of the selected holding."""
from typing import Literal
from pydantic import Field, model_validator
from .wealth import StrictRequest
from ..domain.wealth import units, rate
from ..domain.wealth_limits import MAX_EXECUTION_ITEMS


class RedemptionRequest(StrictRequest):
    schema_version: Literal['wealth-redemption-request-v1'] = 'wealth-redemption-request-v1'
    context_id: str = Field(min_length=1, max_length=100)
    position_key: str = Field(min_length=1, max_length=180)
    input_mode: Literal['units', 'market_value', 'holding_ratio']
    units: str | None = None
    market_value_fen: int | None = Field(default=None, strict=True, gt=0, le=2_000_000_000)
    holding_ratio: str | None = None
    redemption_mode: Literal['standard', 'quick'] = 'standard'

    @model_validator(mode='after')
    def exactly_one(self):
        field = {'units':'units','market_value':'market_value_fen','holding_ratio':'holding_ratio'}[self.input_mode]
        provided = [key for key in ('units','market_value_fen','holding_ratio') if getattr(self,key) is not None]
        if provided != [field]:
            raise ValueError('份额、市值、持仓比例只能填写对应的一项')
        if self.units is not None:
            units(self.units)
        if self.holding_ratio is not None and rate(self.holding_ratio) <= 0:
            raise ValueError('持仓比例须大于0且不超过1')
        return self


class RedemptionBatch(StrictRequest):
    request_id: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    items: list[RedemptionRequest] = Field(min_length=1, max_length=MAX_EXECUTION_ITEMS)

    @model_validator(mode='after')
    def unique_scope(self):
        if len({item.context_id for item in self.items}) != 1:
            raise ValueError('整份赎回必须属于同一理财环境')
        if len({item.position_key for item in self.items}) != len(self.items):
            raise ValueError('同一持仓不能重复选择')
        return self
