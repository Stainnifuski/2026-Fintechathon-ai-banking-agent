"""Server-owned presentation contract; no model-generated HTML or execution."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .bills import BillQuery


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class BillResultTarget(Strict):
    section: Literal['overview','transactions','budgets','checks','forecast','organize']
    subsection: Literal['anomalies','subscriptions','recurring','cashflow','history','merchants','rules'] | None = None
    query: BillQuery | None = None
    tx_id: str | None = None
    tx_ids: list[str] = Field(default_factory=list, max_length=200)
    budget_month: str | None = None
    account_id: str | None = None


class Metric(Strict):
    label: str
    amount_fen: int | None = None
    text: str | None = None


class Point(Strict):
    label: str
    value_fen: int
    target: BillResultTarget | None = None


class Series(Strict):
    name: str
    points: list[Point]


class Chart(Strict):
    title: str
    type: Literal['bar','line']
    series: list[Series]


class ResultRow(Strict):
    title: str
    description: str = ''
    badge: str = ''
    metrics: list[Metric] = Field(default_factory=list)
    progress: float | None = Field(default=None, allow_inf_nan=False)
    target: BillResultTarget | None = None


class BillResultCard(Strict):
    version: Literal[1] = 1
    result_id: str
    request_id: str
    kind: str
    position: int = Field(default=0, ge=0)
    title: str
    summary: str
    as_of: str
    scope: list[str]
    notices: list[str]
    metrics: list[Metric] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    rows: list[ResultRow] = Field(default_factory=list)
    total: int | None = None
    has_more: bool = False
    target: BillResultTarget
    evidence_id: str
    content_hash: str
    compact_reply: bool = False
