"""Read-only billing contracts. All amounts are integer CNY cents."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Period = Literal["month", "year"]
Category = Literal["dining", "shopping", "transport", "entertainment", "subscription", "other"]


class BillQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Period = "month"
    date: str | None = Field(default=None, max_length=7)
    account_id: str | None = Field(default=None, max_length=32)
    category: Category | None = None


class BillMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=500)
    context: BillQuery = Field(default_factory=BillQuery)


class Totals(BaseModel):
    inflow_fen: int = 0
    outflow_fen: int = 0
    net_inflow_fen: int = 0
    consumption_fen: int = 0
    refund_fen: int = 0
    net_consumption_fen: int = 0
    transfer_in_fen: int = 0
    transfer_out_fen: int = 0
    salary_fen: int = 0
    investment_purchase_fen: Literal[0] = Field(default=0, description="兼容字段；理财申购不参与账单报告，恒为0")
    investment_redemption_fen: Literal[0] = Field(default=0, description="兼容字段；理财赎回不参与账单报告，恒为0")
    count: int = 0
    consumption_count: int = 0
    unknown_count: int = 0


class CategoryTotal(BaseModel):
    category: str
    label: str
    amount_fen: int
    count: int
    share_percent: float
    previous_fen: int
    change_fen: int
    change_percent: float | None


class TrendPoint(BaseModel):
    date: str
    consumption_fen: int




class Anomaly(BaseModel):
    kind: Literal["large", "duplicate", "increase"]
    title: str
    reason: str
    tx_ids: list[str] = Field(default_factory=list)
    category: str | None = None


from .bill_insights import BillTransaction, ChangeExplanation


class BillAnalysis(BaseModel):
    query: BillQuery
    excluded_transaction_types: list[str] = Field(default_factory=list, description="不参与本期、对比期、统计、异常、明细和报告文本的银行业务类型")
    timezone: str = "Asia/Shanghai"
    currency: str = "CNY"
    start: datetime
    end_exclusive: datetime
    previous_start: datetime
    previous_end_exclusive: datetime
    as_of: datetime
    partial: bool
    totals: Totals
    previous_totals: Totals
    change_fen: int
    change_percent: float | None
    categories: list[CategoryTotal]
    trend: list[TrendPoint]
    anomalies: list[Anomaly]
    report: list[str]
    notices: list[str]
    change_explanation: ChangeExplanation = Field(default_factory=ChangeExplanation)


class BillPage(BaseModel):
    items: list[BillTransaction]
    total: int
    page: int
    page_size: int


class BillReply(BaseModel):
    kind: Literal["analysis", "clarification"]
    mode: Literal["offline", "model", "fallback"]
    message: str
    analysis: BillAnalysis | None = None
