"""Strict structured input; no natural-language parser or confirmation flags."""
from typing import Literal
from pydantic import Field, model_validator
from .wealth import StrictRequest
from .wealth_returns import ReturnsQuery
from .wealth_redemption import RedemptionRequest

Id = str
class SelectionContext(StrictRequest):
    object_type: Literal['products','orders','positions']
    ids: list[str] = Field(min_length=1,max_length=160)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.ids))!=len(self.ids) or any(not 1<=len(x)<=180 for x in self.ids):raise ValueError('Invalid object references')
        return self

class RecommendationFilter(StrictRequest):
    mode: Literal['general','category','theme']
    category: Literal['quick_redeem','bank_wealth','money_fund','bond_fund','mixed_fund','equity_fund']|None=None
    theme_key: str|None=Field(default=None,min_length=1,max_length=80)
    bucket: Literal['cash_management','stable','growth']|None=None
    @model_validator(mode='after')
    def matching(self):
        if self.mode=='general' and (self.category or self.theme_key):raise ValueError('General filter has no category/theme')
        if self.mode=='category' and (not self.category or self.theme_key):raise ValueError('Category filter requires category only')
        if self.mode=='theme' and (not self.theme_key or self.category):raise ValueError('Theme filter requires theme only')
        return self

class PlanParams(StrictRequest):
    growth_ratio_ex_cash: str|None=None
    @model_validator(mode='after')
    def ratio(self):
        if self.growth_ratio_ex_cash is not None:
            from ..domain.wealth import rate
            rate(self.growth_ratio_ex_cash)
        return self
class RecommendParams(StrictRequest):
    filter: RecommendationFilter|None=None
    cursor: str|None=Field(default=None,max_length=128)
    selected_family_ids: list[str]|None=Field(default=None,max_length=160)
class SelectionItem(StrictRequest):
    product_id: str=Field(min_length=1,max_length=120)
    product_version: int=Field(strict=True,ge=1)
    share_class: str
    target_ratio_total: str
    @model_validator(mode='after')
    def ratio(self):
        from ..domain.wealth import rate
        rate(self.target_ratio_total)
        return self
class PortfolioSelection(StrictRequest):
    portfolio_id: str
    snapshot_id: str
    context_id: str
    expected_version: int=Field(strict=True,ge=1)
    items: list[SelectionItem]=Field(min_length=1,max_length=160)
class ReviseParams(StrictRequest):
    selection: PortfolioSelection|None=None
class DetailParams(StrictRequest):
    product_id: str|None=Field(default=None,min_length=1,max_length=120)
    product_version: int|None=Field(default=None,strict=True,ge=1)
    period: Literal['1m','3m','1y']='1y'
class CompareParams(StrictRequest):
    product_ids: list[str]|None=Field(default=None,min_length=2,max_length=4)
    @model_validator(mode='after')
    def unique(self):
        if self.product_ids and len(set(self.product_ids))!=len(self.product_ids):raise ValueError('Duplicate products')
        return self
class ListParams(StrictRequest):
    page: int|None=Field(default=None,strict=True,ge=1)
    page_size: int|None=Field(default=None,strict=True,ge=1,le=100)
class PrepareParams(StrictRequest):
    portfolio_id: str|None=None
    expected_version: int|None=Field(default=None,strict=True,ge=1)
    snapshot_id: str|None=None
class RedemptionDraft(StrictRequest):
    schema_version: Literal['wealth-redemption-request-v1']='wealth-redemption-request-v1'
    context_id: str
    position_key: str|None=None
    input_mode: Literal['units','market_value','holding_ratio']|None=None
    units: str|None=None
    market_value_fen: int|None=Field(default=None,strict=True,gt=0,le=2_000_000_000)
    holding_ratio: str|None=None
    redemption_mode: Literal['standard','quick']='standard'
    @model_validator(mode='after')
    def expression(self):
        if any(x is not None for x in (self.units,self.market_value_fen,self.holding_ratio)):
            if not self.input_mode:raise ValueError('Input mode required')
            RedemptionRequest.model_validate({**self.model_dump(),'position_key':self.position_key or 'pending'})
        return self
class RedeemParams(StrictRequest):
    redemption: RedemptionDraft|None=None
class CancelParams(StrictRequest):
    operation_ids: list[str]|None=Field(default=None,min_length=1,max_length=160)
    @model_validator(mode='after')
    def unique(self):
        if self.operation_ids and len(set(self.operation_ids))!=len(self.operation_ids):raise ValueError('Duplicate orders')
        return self

PARAMS={'plan_portfolio':PlanParams,'recommend_products':RecommendParams,'revise_portfolio':ReviseParams,
        'show_product_detail':DetailParams,'compare_products':CompareParams,'list_orders':ListParams,
        'list_positions':ListParams,'show_wealth_returns':ReturnsQuery,'prepare_purchase':PrepareParams,
        'preview_redemption':RedeemParams,'preview_order_cancellation':CancelParams}
class WealthActionV1(StrictRequest):
    schema_version: Literal['wealth-action-v1']='wealth-action-v1'
    request_id: str=Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    context_id: str=Field(min_length=1,max_length=100)
    session_id: str|None=Field(default=None,min_length=1,max_length=64)
    expected_session_version: int|None=Field(default=None,strict=True,ge=1)
    selection_context: SelectionContext|None=None
    action: Literal['plan_portfolio','recommend_products','revise_portfolio','show_product_detail','compare_products','list_orders','list_positions','show_wealth_returns','prepare_purchase','preview_redemption','preview_order_cancellation']
    params: dict=Field(default_factory=dict)
    @model_validator(mode='after')
    def strict_params(self):
        if bool(self.session_id)!=(self.expected_session_version is not None):raise ValueError('Session and expected version required together')
        self.params=PARAMS[self.action].model_validate(self.params).model_dump(mode='json')
        return self

class RequiredField(StrictRequest):
    field: str
    reason: str
    options: list[dict]|None=None
class WealthActionResultV1(StrictRequest):
    schema_version: Literal['wealth-action-result-v1']='wealth-action-result-v1'
    request_id: str
    context_id: str
    session_id: str
    session_version: int
    action: str
    status: Literal['ok','clarify','blocked']
    message: str
    data: dict|list|None
    required_fields: list[RequiredField]=Field(default_factory=list)
    allowed_actions: list[str]=Field(default_factory=list)
