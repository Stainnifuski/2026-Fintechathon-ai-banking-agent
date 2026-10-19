from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .bills import Category, BillQuery
from .bill_insights import CashflowScenario

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class ClassificationUpdate(Strict):
    category: Category | None
    apply_to_merchant: bool = False
    expected_version: int = Field(ge=0)
    expected_rule_version: int = Field(default=0, ge=0)

class RuleDelete(Strict):
    expected_version: int = Field(ge=1)

class BudgetUpdate(Strict):
    month: str = Field(pattern=r'^\d{4}-\d{2}$')
    account_id: str | None = Field(default=None, max_length=32)
    category: Category | None = None
    amount_fen: int = Field(gt=0, le=100_000_000_000_000)
    warning_percent: int = Field(default=80, ge=1, le=100)
    active: bool = True
    expected_version: int = Field(default=0, ge=0)

class AnomalyReview(Strict):
    query: BillQuery
    as_of: datetime | None = Field(default=None, strict=False)
    anomaly_id: str = Field(min_length=1, max_length=40)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['pending','normal','resolved']
    note: str = Field(default='', max_length=256)
    expected_version: int = Field(ge=0)

class RecurringPlan(Strict):
    name: str = Field(min_length=1,max_length=64)
    account_id: str = Field(min_length=1,max_length=32)
    kind: Literal['income','expense']
    frequency: Literal['WEEKLY','MONTHLY','QUARTERLY','YEARLY']
    amount_fen: int = Field(gt=0,le=100_000_000_000_000)
    anchor_at: datetime = Field(strict=False)
    month_end: bool = False
    category: Category = 'other'
    enabled: bool = True
    active: bool = True
    expected_version: int = Field(default=0,ge=0)

    @field_validator('anchor_at')
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None: raise ValueError('日期必须带时区')
        if not 2000 <= value.year <= 2099: raise ValueError('日期范围2000至2099年')
        return value

class MergeApply(Strict):
    source_merchant_id: str = Field(min_length=1,max_length=40)
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$')

class SplitSpec(Strict):
    name: str = Field(min_length=1,max_length=64)
    tx_ids: list[str] = Field(min_length=1,max_length=200)
    aliases: list[str] = Field(default_factory=list,max_length=20)

class MerchantSplit(SplitSpec):
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$')

class ForecastSave(Strict):
    scenario: CashflowScenario
