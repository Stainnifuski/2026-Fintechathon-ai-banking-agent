"""01内部生命周期输入；事实、模型及身份仅由服务端提供。"""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from .wealth_portfolio import DTO, Version, ratio
from .wealth_portfolio_candidates import CandidateFilter

class SelectedShare(DTO):
    product_id: str
    product_version: Version
    share_class: Literal['A','C'] | None

class CandidateBinding(DTO):
    session_id: str
    expected_version: Version
    filter: CandidateFilter

class PortfolioCreate(DTO):
    cash_reserve_fen: int | None = Field(default=None, strict=True, ge=0)
    available_cash_fen: int | None = Field(default=None, strict=True, ge=0)
    context_id: str
    growth_ratio_ex_cash: str | None = None
    selected: list[SelectedShare] | None = None
    candidate_binding: CandidateBinding | None = None
    _ratio=field_validator('growth_ratio_ex_cash')(lambda v:ratio(v) if v is not None else v)

class PortfolioRecommendation(DTO):
    context_id: str
    filter: CandidateFilter
    cursor: str | None = None
    candidate_binding: CandidateBinding | None = None
    selected: list[SelectedShare] = Field(default_factory=list)
    excluded_family_ids: list[str] = Field(default_factory=list, max_length=2000)
    @model_validator(mode='after')
    def cursor_binding(self):
        if self.cursor and self.candidate_binding is None:raise ValueError('cursor requires session binding')
        return self
