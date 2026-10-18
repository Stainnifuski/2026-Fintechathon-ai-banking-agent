"""Explicit amounts for selected products; no asset allocation input."""
from pydantic import Field, model_validator
from .wealth import StrictRequest
from ..domain.wealth_limits import MAX_EXECUTION_ITEMS


class TargetPurchaseItem(StrictRequest):
    product_id: str = Field(min_length=1, max_length=32)
    product_version: int = Field(ge=1, strict=True)
    disclosure_version: int = Field(ge=1, strict=True)
    amount_fen: int = Field(gt=0, le=2_000_000_000, strict=True)


class TargetPurchaseBatch(StrictRequest):
    request_id: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    context_id: str = Field(min_length=1, max_length=100)
    items: list[TargetPurchaseItem] = Field(min_length=1, max_length=MAX_EXECUTION_ITEMS)

    @model_validator(mode='after')
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError('同一产品不能重复申购，请合并本人指定金额')
        return self
