"""Evidence-backed spending explanations and read-only cashflow scenarios."""
from datetime import datetime
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
class BillTransaction(BaseModel):
    tx_id: str
    account_id: str
    type: str
    category: str
    amount_fen: int
    timestamp: datetime
    counterparty: str
    note: str | None
    status: str
    merchant_id: str | None = None
    merchant_name: str | None = None
    original_tx_id: str | None = None
    refund_link_source: str | None = None
    original_category: str | None = None
    refund_tx_ids: list[str] = Field(default_factory=list)
    transfer_pair_id: str | None = None
    paired_tx_id: str | None = None
    recurring_stream_id: str | None = None
    tags: list[dict[str, str]] = Field(default_factory=list)
    annotation_version: int = 0
    raw_category: str | None = None
    effective_category: str | None = None
    category_source: str = "original"
    classification_version: int = 0
    category_rule_version: int = 0
    suggested_category: str | None = None
    suggestion_reason: str | None = None
    refunded_fen: int = 0
    refundable_fen: int | None = None



class ChangeDriver(BaseModel):
    category: str
    merchant: str
    current_fen: int
    previous_fen: int
    current_count: int
    previous_count: int
    change_fen: int
    frequency_effect_fen: int
    unit_amount_effect_fen: int
    entry_exit_effect_fen: int
    new_large_single: bool
    explanation: str
    current_evidence: list[BillTransaction]
    previous_evidence: list[BillTransaction]


class ChangeExplanation(BaseModel):
    total_change_fen: int = 0
    frequency_effect_fen: int = 0
    unit_amount_effect_fen: int = 0
    entry_exit_effect_fen: int = 0
    drivers: list[ChangeDriver] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class SubscriptionInsight(BaseModel):
    account_id: str
    merchant: str
    subscription_ids: list[str]
    active_subscription_ids: list[str]
    configured_monthly_fen: int
    current_fen: int
    previous_fen: int
    current_count: int
    previous_count: int
    flags: list[str]
    explanations: list[str]
    current_evidence: list[BillTransaction]
    previous_evidence: list[BillTransaction]


class SubscriptionInsights(BaseModel):
    as_of: datetime
    items: list[SubscriptionInsight]
    notices: list[str]


class CategoryReduction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal['dining', 'shopping', 'transport', 'entertainment', 'education', 'other']
    percent: int = Field(ge=0, le=100)


class OneOffEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    at: datetime = Field(strict=False)
    label: str = Field(min_length=1,max_length=64)
    amount_fen: int = Field(ge=-100_000_000_000_000,le=100_000_000_000_000)


class CashflowScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    days: int = Field(default=30, ge=1, le=62)
    account_id: str | None = Field(default=None, min_length=1, max_length=32)
    reductions: list[CategoryReduction] = Field(default_factory=list, max_length=6)
    one_off_events: list[OneOffEvent] = Field(default_factory=list,max_length=20)
    excluded_subscription_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=30)


class CashflowEvent(BaseModel):
    at: datetime
    label: str
    amount_fen: int
    source: Literal['subscription_plan', 'scheduled_transfer', 'salary_estimate', 'recurring_estimate', 'recurring_plan', 'scenario']
    reference_id: str | None = None
    evidence_tx_ids: list[str] = Field(default_factory=list)
    excluded_in_scenario: bool = False


class SpendingBaseline(BaseModel):
    category: str
    daily_fen: int
    sample_count: int
    observed_months: int
    estimated_total_fen: int
    scenario_total_fen: int


class CashflowPoint(BaseModel):
    date: str
    planned_balance_fen: int
    baseline_balance_fen: int
    scenario_balance_fen: int
    historical_low_balance_fen: int
    historical_high_balance_fen: int


class CashflowProjection(BaseModel):
    as_of: datetime
    end_exclusive: datetime
    account_id: str | None
    days: int
    opening_balance_fen: int
    held_fen: int
    opening_available_fen: int
    planned_outflow_fen: int
    estimated_recurring_spending_fen: int = 0
    estimated_income_fen: int
    estimated_variable_spending_fen: int
    baseline_end_fen: int
    scenario_end_fen: int
    scenario_saving_fen: int
    calibration: dict = Field(default_factory=dict)
    minimum_baseline_fen: int
    first_negative_date: str | None
    scenario: CashflowScenario
    spending_baselines: list[SpendingBaseline]
    events: list[CashflowEvent]
    timeline: list[CashflowPoint]
    notices: list[str]
