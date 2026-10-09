"""B-owned AssetSummary contract, shared by overview and A's snapshot adapter."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: Literal['wealth-asset-summary-v1'] = 'wealth-asset-summary-v1'
    asset_scope_id: str
    account_ids: list[str]
    snapshot_id: str
    as_of: datetime
    business_at: datetime
    cash_balance_fen: int = Field(ge=0, strict=True)
    held_fen: int = Field(ge=0, strict=True)
    available_cash_fen: int = Field(ge=0, strict=True)
    positions_market_value_fen: int | None = Field(ge=0, strict=True)
    redemption_receivable_fen: int = Field(ge=0, strict=True)
    total_assets_fen: int | None = Field(ge=0, strict=True)
    valuation_complete: bool = Field(strict=True)
    missing_fields: list[str]
    data_source: str

    @model_validator(mode='after')
    def coherent(self):
        if self.account_ids != sorted(set(self.account_ids)):
            raise ValueError('account_ids must be unique and sorted')
        if self.cash_balance_fen != self.held_fen + self.available_cash_fen:
            raise ValueError('held cash is a subset of cash balance')
        if self.as_of.tzinfo is None or self.business_at.tzinfo is None:
            raise ValueError('asset timestamps require a timezone')
        if self.valuation_complete:
            if self.positions_market_value_fen is None or self.missing_fields:
                raise ValueError('complete valuation requires all position values')
            expected = self.cash_balance_fen + self.positions_market_value_fen + self.redemption_receivable_fen
            if self.total_assets_fen != expected:
                raise ValueError('total must equal cash + positions + receivable')
        elif self.positions_market_value_fen is not None or self.total_assets_fen is not None or not self.missing_fields:
            raise ValueError('incomplete valuation must retain null totals and missing fields')
        return self
