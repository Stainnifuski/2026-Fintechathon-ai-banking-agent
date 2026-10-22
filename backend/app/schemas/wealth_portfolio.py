"""A0共用JSON事实契约；不加入单次预算、必填期限或授权签发字段。"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Any, Self
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Fen = Annotated[int, Field(strict=True, ge=0)]
Version = Annotated[int, Field(strict=True, ge=1)]
Risk = Annotated[int, Field(strict=True, ge=1, le=5)]
Bucket = Literal['cash_management', 'stable', 'growth']


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    @field_validator('*', mode='after')
    @classmethod
    def aware_time(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('time requires timezone')
            return value.astimezone(timezone.utc)
        return value


def decimal_number(value: str) -> Decimal:
    """非法数字统一转换为校验错误，避免Decimal异常逃逸为500。"""
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError('invalid decimal string') from error


def ratio(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('ratio must be decimal string in 0..1')
    number = decimal_number(value)
    if not number.is_finite() or not 0 <= number <= 1:
        raise ValueError('ratio outside 0..1')
    return value


class AccountFact(DTO):
    account_id: str
    account_type: Literal['savings', 'checking']
    balance_fen: Fen
    held_fen: Fen
    available_fen: Fen

    @model_validator(mode='after')
    def cash(self) -> Self:
        if self.balance_fen - self.held_fen != self.available_fen:
            raise ValueError('cash balance-held must equal available')
        return self


class AssetSummary(DTO):
    """00 §6.10 shared with B, not a replacement bank implementation."""
    schema_version: Literal['wealth-asset-summary-v1'] = 'wealth-asset-summary-v1'
    asset_scope_id: str
    account_ids: list[str]
    snapshot_id: str
    as_of: datetime
    business_at: datetime
    cash_balance_fen: Fen
    held_fen: Fen
    available_cash_fen: Fen
    positions_market_value_fen: Fen | None
    redemption_receivable_fen: Fen | None
    total_assets_fen: Fen | None
    valuation_complete: bool = Field(strict=True)
    missing_fields: list[str]
    data_source: str

    @model_validator(mode='after')
    def totals(self) -> Self:
        if len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError('unique authorized accounts required')
        if not self.account_ids and any(value not in (None,0) for value in (self.cash_balance_fen,self.held_fen,self.available_cash_fen,self.positions_market_value_fen,self.redemption_receivable_fen,self.total_assets_fen)):
            raise ValueError('an empty authorized scope cannot contain money')
        if self.cash_balance_fen-self.held_fen != self.available_cash_fen:
            raise ValueError('invalid payable cash')
        if self.valuation_complete:
            if self.positions_market_value_fen is None or self.redemption_receivable_fen is None:
                raise ValueError('complete valuation cannot contain unknown amounts')
            if self.total_assets_fen != self.cash_balance_fen+self.positions_market_value_fen+self.redemption_receivable_fen:
                raise ValueError('total assets cash+positions+receivable; held is not added/subtracted')
        elif self.total_assets_fen is not None or not self.missing_fields:
            raise ValueError('incomplete total must be null with missing reason')
        if not self.valuation_complete and any('market_value_fen' in reason for reason in self.missing_fields) and self.positions_market_value_fen is not None:
            raise ValueError('missing position values cannot be replaced by zero')
        return self


class AssessmentFact(DTO):
    assessment_id: str
    capacity_level: Risk
    purchase_allowed: bool = Field(strict=True)
    expires_at: datetime
    confirmed: bool = Field(strict=True)


class PositionFact(DTO):
    position_key: str
    account_id: str
    product_id: str
    product_version: Version
    lot_ids: list[str]
    units: str
    redeemable_units: str
    market_value_fen: Fen | None
    bucket: Bucket | None
    nav: str | None
    nav_date: date | None

    @field_validator('units', 'redeemable_units')
    @classmethod
    def quantities(cls, value: str) -> str:
        import re
        if not re.fullmatch(r'\d+\.\d{4}', value) or not Decimal(value).is_finite():
            raise ValueError('units require nonnegative four-place string')
        return value

    @field_validator('nav')
    @classmethod
    def price(cls, value: str | None) -> str | None:
        if value is not None and (not decimal_number(value).is_finite() or decimal_number(value) <= 0):
            raise ValueError('NAV must be positive')
        return value

    @model_validator(mode='after')
    def valid_position(self) -> Self:
        if Decimal(self.redeemable_units) > Decimal(self.units) or len(set(self.lot_ids)) != len(self.lot_ids):
            raise ValueError('invalid lot or redeemable units')
        if self.market_value_fen is not None and (self.nav is None or self.nav_date is None):
            raise ValueError('known valuation requires NAV/date')
        return self


class AssetSnapshot(DTO):
    schema_version: Literal['wealth-asset-snapshot-v1']
    snapshot_id: str
    context_id: str | None
    context_epoch: Version | None
    asset_scope_id: str
    account_ids: list[str]
    total_assets_fen: Fen | None
    as_of: datetime
    business_at: datetime
    basis_kind: Literal['linked_bank_assets']
    basis_status: Literal['confirmed', 'pending_decision', 'fixture_only']
    basis_fen: Fen | None
    valuation_complete: bool = Field(strict=True)
    accounts: list[AccountFact]
    positions: list[PositionFact]
    receivable_fen: Fen | None
    orders: list[dict]
    assessment: AssessmentFact | None
    data_versions: dict
    missing_fields: list[str]

    @model_validator(mode='after')
    def consistency(self) -> Self:
        if len(set(self.account_ids)) != len(self.account_ids) or set(self.account_ids) != {a.account_id for a in self.accounts} or len(self.accounts) != len(self.account_ids):
            raise ValueError('snapshot account scope mismatch')
        if len({p.position_key for p in self.positions}) != len(self.positions):
            raise ValueError('duplicate position')
        lots = [lot for p in self.positions for lot in p.lot_ids]
        if len(lots) != len(set(lots)) or any(p.account_id not in self.account_ids for p in self.positions):
            raise ValueError('duplicate lot or out-of-scope position')
        if any(o.get('account_id') not in self.account_ids for o in self.orders):
            raise ValueError('out-of-scope order')
        cutoff = self.as_of.astimezone(ZoneInfo('Asia/Shanghai')).date()
        if any(p.nav_date and (p.nav_date > self.business_at.astimezone(ZoneInfo('Asia/Shanghai')).date() or p.nav_date > cutoff) for p in self.positions):
            raise ValueError('future NAV')
        if self.basis_fen != self.total_assets_fen:
            raise ValueError('basis and summary total must be identical')
        if self.valuation_complete:
            if self.receivable_fen is None or any(p.market_value_fen is None for p in self.positions):
                raise ValueError('unknown valuation')
            total = sum(a.balance_fen for a in self.accounts)+sum(p.market_value_fen for p in self.positions)+self.receivable_fen
            if total != self.total_assets_fen:
                raise ValueError('inconsistent asset total')
        elif self.total_assets_fen is not None or not self.missing_fields:
            raise ValueError('incomplete snapshot must expose null and reason')
        return self


class PerformancePoint(DTO):
    metric: Literal['total_return', 'nav_change', 'seven_day_annualized', 'benchmark', 'daily_nav_change']
    value_percent: str | None
    start_date: date | None = None
    end_date: date | None = None
    nav_date: date | None = None
    previous_nav_date: date | None = None
    status: Literal['available', 'unavailable', 'partial', 'not_applicable']
    reason: str | None
    source: str | None = None

    @field_validator('value_percent')
    @classmethod
    def finite_percent(cls, value: str | None) -> str | None:
        if value is not None and not decimal_number(value).is_finite():
            raise ValueError('performance percentage must be finite')
        return value

    @model_validator(mode='after')
    def availability(self) -> Self:
        if self.status == 'available' and self.value_percent is None:
            raise ValueError('available performance needs actual value')
        if self.status in ('unavailable', 'not_applicable') and (self.value_percent is not None or not self.reason):
            raise ValueError('unknown performance requires null and reason')
        return self


class Performance(DTO):
    one_year: PerformancePoint
    latest_day: PerformancePoint


class Fees(DTO):
    purchase_fee_rate: str | None
    sales_service_fee_rate: str | None
    redemption_fee_rules: list[dict] | None
    missing_fields: list[str] = Field(default_factory=list)
    _ratio = field_validator('purchase_fee_rate', 'sales_service_fee_rate')(lambda v: ratio(v) if v is not None else v)


class ShareClass(DTO):
    product_id: str
    product_version: Version
    disclosure_version: Version
    share_class: Literal['A', 'C'] | None
    product_risk_level: Risk | None
    status: str
    tradable: bool = Field(strict=True)
    min_amount_fen: Fen | None
    fees: Fees
    performance: Performance
    detail_ref: str
    data_source: str | None
    data_as_of: datetime | None


class ProductCandidate(DTO):
    family_id: str
    name: str
    bucket: Bucket
    reason: str
    reason_codes: list[str]
    evidence: dict
    share_classes: list[ShareClass]

    @model_validator(mode='after')
    def distinct_shares(self) -> Self:
        keys = [(s.product_id, s.product_version) for s in self.share_classes]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError('unique share references required')
        return self


class RecommendationFilter(DTO):
    mode: Literal['general', 'category', 'name', 'theme']
    name_query: str | None = Field(default=None, min_length=1, max_length=120)
    category: Literal['quick_redeem', 'bank_wealth',  'bond_fund', 'mixed_fund', 'equity_fund'] | None = None
    theme_key: str | None = None
    bucket: Bucket | None = None

    theme_keys: list[str] = Field(default_factory=list)
    theme_operator: Literal['AND', 'OR'] | None = None

    @model_validator(mode='after')
    def mode_fields(self) -> Self:
        if self.mode == 'name':
            if not self.name_query or not self.name_query.strip() or self.category or self.bucket or self.theme_key or self.theme_keys or self.theme_operator:
                raise ValueError('name matching requires only a nonblank product name or code')
            self.name_query = self.name_query.strip()
            return self
        if self.name_query is not None:
            raise ValueError('name query only applies to name mode')
        if self.theme_keys:
            if self.mode != 'theme' or self.theme_key or self.category or self.theme_operator is None or any(not k.strip() or k != k.strip() for k in self.theme_keys) or len(set(self.theme_keys)) != len(self.theme_keys):
                raise ValueError('explicit distinct multi-theme filter required')
            return self
        if self.theme_operator is not None:
            raise ValueError('theme operator requires theme_keys')
        if (self.mode == 'general' and (self.category or self.theme_key)
            or self.mode == 'category' and (not self.category or self.theme_key)
            or self.mode == 'theme' and (not self.theme_key or self.category)):
            raise ValueError('filter mode/fields mismatch')
        return self


class CandidateGroup(DTO):
    session_binding: dict | None = None
    reason_codes: list[str] = Field(default_factory=list)
    bucket: Bucket | None
    filter: RecommendationFilter
    status: Literal['ok', 'no_theme_match', 'no_category_match', 'no_eligible_products', 'risk_mismatch', 'unavailable']
    items: list[ProductCandidate]
    next_cursor: str | None = None
    risk_explanation: dict | None = None

    @model_validator(mode='after')
    def families(self) -> Self:
        if len({c.family_id for c in self.items}) != len(self.items):
            raise ValueError('duplicate family')
        if self.status == 'ok' and self.risk_explanation is not None:
            raise ValueError('eligible result must not carry excluded risk warning')
        if self.status == 'risk_mismatch' and not self.risk_explanation:
            raise ValueError('risk mismatch requires evidence')
        return self


class UserPreferences(DTO):
    growth_ratio_ex_cash: str | None = None
    _ratio = field_validator('growth_ratio_ex_cash')(lambda v: ratio(v) if v is not None else v)


class PortfolioInput(DTO):
    schema_version: Literal['wealth-portfolio-input-v1']
    snapshot: AssetSnapshot
    products: list[ProductCandidate]
    features: dict
    user_preferences: UserPreferences

    @model_validator(mode='after')
    def distinct_families(self) -> Self:
        if len({p.family_id for p in self.products}) != len(self.products):
            raise ValueError('duplicate input family')
        return self


class BucketTarget(DTO):
    bucket: Bucket
    min_ratio: str
    target_ratio: str
    max_ratio: str
    target_fen: Fen
    current_fen: Fen | None
    reason: str
    _ratio = field_validator('min_ratio', 'target_ratio', 'max_ratio')(ratio)

    @model_validator(mode='after')
    def interval(self) -> Self:
        if not Decimal(self.min_ratio) <= Decimal(self.target_ratio) <= Decimal(self.max_ratio):
            raise ValueError('target outside interval')
        return self


class SelectionItem(DTO):
    product_id: str
    product_version: Version
    share_class: Literal['A', 'C'] | None
    target_ratio_total: str
    _ratio = field_validator('target_ratio_total')(ratio)


class PortfolioSelection(DTO):
    portfolio_id: str
    expected_version: Version
    snapshot_id: str
    context_id: str | None
    items: list[SelectionItem]
    revision_kind: Literal['adjustment', 'preference'] = 'adjustment'
    cash_residual_fen: Fen | None = None
    growth_ratio_ex_cash: str | None = None
    _ratio = field_validator('growth_ratio_ex_cash')(lambda v: ratio(v) if v is not None else v)

    @model_validator(mode='after')
    def unique_items(self) -> Self:
        keys = [(i.product_id, i.product_version) for i in self.items]
        if len(keys) != len(set(keys)) or sum(Decimal(i.target_ratio_total) for i in self.items) > 1:
            raise ValueError('duplicate selection or total ratio exceeds one')
        return self


class AdvisedPosition(DTO):
    product_id: str
    product_version: Version
    disclosure_version: Version | None = None
    share_class: Literal['A', 'C'] | None
    original_share_class: str | None
    family_id: str
    name: str
    bucket: Bucket
    target_fen: Fen
    current_fen: Fen
    role: Literal['advice', 'selected', 'holding']
    account_ids: list[str]
    holding_cap_fen: Fen | None
    fee_status: Literal['unknown'] = 'unknown'
    market_status: Literal['unknown'] = 'unknown'


class PortfolioModelView(DTO):
    initial_ratios: list[str]
    slider_bounds: list[str] | None
    slider_step: str | None = None
    slider_reason: str | None = None
    adjustment_contract: dict | None = None
    available_cash_fen: Fen
    receivable_fen: Fen
    held_fen: Fen
    snapshot: AssetSnapshot
    financial_effect_status: Literal['not_evaluated'] = 'not_evaluated'


class PortfolioPlan(DTO):
    schema_version: Literal['wealth-portfolio-plan-v1']
    portfolio_id: str
    version: Version
    snapshot_id: str
    asset_scope_id: str
    context_id: str | None
    as_of: datetime
    basis_kind: Literal['linked_bank_assets']
    basis_status: Literal['confirmed', 'pending_decision', 'fixture_only']
    basis_fen: Fen
    provider_id: str
    provider_version: str
    model_status: Literal['fixture', 'rule_baseline', 'researched_model']
    buckets: list[BucketTarget]
    growth_ratio_ex_cash: str | None
    candidate_groups: list[CandidateGroup]
    selected_positions: list[SelectionItem]
    cash_residual_fen: Fen
    data_versions: dict
    missing_fields: list[str]
    reasons: list[str]
    advised_positions: list[AdvisedPosition] = Field(default_factory=list)
    model_view: PortfolioModelView | None = None
    planning: dict | None = None
    planning_reference: dict | None = None
    _ratio = field_validator('growth_ratio_ex_cash')(lambda v: ratio(v) if v is not None else v)

    @model_validator(mode='after')
    def contract_arithmetic(self) -> Self:
        if {b.bucket for b in self.buckets} != {'cash_management', 'stable', 'growth'} or len(self.buckets) != 3:
            raise ValueError('three unique buckets required')
        if sum(Decimal(b.target_ratio) for b in self.buckets) != 1:
            raise ValueError('targets must total one')
        if not sum(Decimal(b.min_ratio) for b in self.buckets) <= 1 <= sum(Decimal(b.max_ratio) for b in self.buckets):
            raise ValueError('infeasible intervals')
        if sum(b.target_fen for b in self.buckets) != self.basis_fen:
            raise ValueError('target amounts must total basis')
        for b in self.buckets:
            if abs(Decimal(b.target_ratio)*self.basis_fen-b.target_fen) > 1:
                raise ValueError('ratio/amount mismatch beyond fen rounding')
        cash = next(b for b in self.buckets if b.bucket == 'cash_management')
        growth = next(b for b in self.buckets if b.bucket == 'growth')
        denominator = self.basis_fen-cash.target_fen
        if denominator == 0:
            if self.growth_ratio_ex_cash is not None:
                raise ValueError('zero slider denominator must be null')
        elif self.growth_ratio_ex_cash is None or abs(Decimal(self.growth_ratio_ex_cash)-Decimal(growth.target_fen)/denominator) > Decimal('0.000001'):
            raise ValueError('slider denominator excludes cash management')
        if self.basis_status == 'fixture_only' and self.model_status != 'fixture':
            raise ValueError('fixture is not a researched model')
        return self
