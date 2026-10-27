"""Model-proposed bill preferences. Only the authenticated confirmation API applies them."""
from typing import Annotated, Literal, Union
from pydantic import Field
from .nova import StrictModel
from .bill_features import (
    ClassificationUpdate, RuleDelete, BudgetUpdate, AnomalyReview, RecurringPlan,
    MergeApply, MerchantSplit, ForecastSave,
)
from .bill_foundation import AnnotationUpdate, RecurringUpdate, MerchantAliasCreate


class TagsChange(AnnotationUpdate):
    action: Literal['tags']
    tx_id: str | None = Field(default=None, min_length=1, max_length=32)


class ClassificationChange(ClassificationUpdate):
    action: Literal['classification']
    tx_id: str | None = Field(default=None, min_length=1, max_length=32)


class RuleChange(RuleDelete):
    action: Literal['remove_category_rule']
    merchant_id: str = Field(min_length=1, max_length=40)


class BudgetChange(BudgetUpdate):
    action: Literal['budget']


class ReviewChange(AnomalyReview):
    action: Literal['anomaly_review']


class PlanChange(RecurringPlan):
    action: Literal['recurring_plan']
    stream_id: str | None = Field(default=None, min_length=1, max_length=40)


class RecurringChange(RecurringUpdate):
    action: Literal['recurring_enabled']
    stream_id: str = Field(min_length=1, max_length=40)


class AliasChange(MerchantAliasCreate):
    action: Literal['merchant_alias']
    merchant_id: str = Field(min_length=1, max_length=40)


class MergeChange(MergeApply):
    action: Literal['merchant_merge']
    merchant_id: str = Field(min_length=1, max_length=40)


class SplitChange(MerchantSplit):
    action: Literal['merchant_split']
    merchant_id: str = Field(min_length=1, max_length=40)


class UndoChange(StrictModel):
    action: Literal['merchant_undo']
    event_id: str = Field(min_length=1, max_length=40)


class ForecastChange(ForecastSave):
    action: Literal['save_forecast']


class RefreshChange(StrictModel):
    action: Literal['refresh_foundations']


BillChange = Annotated[Union[
    TagsChange, ClassificationChange, RuleChange, BudgetChange, ReviewChange,
    PlanChange, RecurringChange, AliasChange, MergeChange, SplitChange, UndoChange,
    ForecastChange, RefreshChange,
], Field(discriminator='action')]


class PrepareBillChange(StrictModel):
    request_quote: str = Field(min_length=1, max_length=2000,
        description='本轮用户要求修改的连续原文，逐字引用。查询不生成修改卡；参数不明确时先追问。')
    change: BillChange


class BillCardDecision(StrictModel):
    decision: Literal['confirm', 'cancel']
