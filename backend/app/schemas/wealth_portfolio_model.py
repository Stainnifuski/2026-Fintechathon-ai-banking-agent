"""01独立计算输出封装；模型元数据与旧PortfolioPlan契约分开。"""
from typing import Literal
from pydantic import Field
from .wealth_portfolio import DTO,PortfolioPlan,CandidateGroup


class ModelResult(DTO):
    status: Literal['ok','partial','unavailable','no_configurable_assets']
    plan: PortfolioPlan | None = None
    candidate_groups: list[CandidateGroup] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    financial_effect_status: Literal['not_evaluated']='not_evaluated'
