"""01共用资格和候选协议；请求只给引用/预期版本，不带风险或购买事实。"""
from datetime import datetime,date
from typing import Annotated, Literal, Self
from pydantic import Field, field_validator, model_validator
from .wealth_portfolio import DTO, Fen, ratio, RecommendationFilter,Fees
from .wealth_portfolio_data import VersionValue

SourceKind=Literal['real','simulated','fixture']
CandidateStatus=Literal['ok','no_theme_match','no_category_match','no_eligible_products','risk_mismatch','unavailable']

class FormalAssessment(DTO):
    user_id: str
    assessment_id: str | None
    version: VersionValue | None
    source_id: str
    source_kind: SourceKind
    capacity_level: Annotated[int,Field(strict=True)] | None
    confirmed: bool = Field(strict=True)
    purchase_allowed: bool = Field(strict=True)
    expires_at: datetime | None
    read_at: datetime

class CurrentSimulationEvidence(DTO):
    provenance: Literal['protected-local-bank-current-products']
    product_id: str
    product_version: VersionValue
    disclosure_version: VersionValue
    observed_at: datetime
    business_at: datetime
    calendar_version: str
    cutoff: str
    open_interval_days: Annotated[int,Field(strict=True,ge=1)]
    purchase_end_date: date | None = None
    nav_date: date | None
    available_at: datetime | None
    nav: str | None
    status: str
    purchase_paused: bool = Field(strict=True)
    purchase_open: bool = Field(strict=True)
    data_stale: bool = Field(strict=True)
    product_content_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    pricing_mode: str | None = None

class BankPurchaseFact(DTO):
    product_id: str
    share_class: str | None
    product_version: VersionValue | None
    disclosure_version: VersionValue | None
    rules_version: VersionValue | None
    source_id: str
    source_kind: SourceKind
    channel: str
    valid_from: datetime | None
    valid_until: datetime | None
    purchase_allowed: bool | None = Field(strict=True)
    rules_complete: bool = Field(strict=True)
    minimum_fen: Fen | None
    maximum_fen: Fen | None
    limit_kind: Literal['bounded','unbounded','unknown']
    risk_level: Annotated[int,Field(strict=True)] | None
    risk_current_confirmed: bool = Field(strict=True)
    risk_evidence_id: str | None
    holding_allowed: bool | None = Field(default=None,strict=True)
    redemption_allowed: bool | None = Field(default=None,strict=True)
    current_simulation: CurrentSimulationEvidence | None = None
    fee_rules: Fees | None = None # read-only current version; not a qualification prerequisite
    execution_policy: dict | None = None

class CurrentFacts(DTO):
    user_id: str
    source_id: str
    mode: SourceKind
    channel: str
    read_at: datetime
    data_version: str
    daily_version: str
    offers_version: VersionValue | None
    assessment: FormalAssessment | None
    latest_assessment_id: str | None
    latest_assessment_version: VersionValue | None
    assessment_current_proven: bool = Field(strict=True)
    offers: dict[str,BankPurchaseFact]

class VersionContext(DTO):
    user_id: str
    source_id: str
    mode: SourceKind
    channel: str
    data_version: str
    daily_version: str
    assessment_id: str | None
    assessment_version: VersionValue | None
    offers_version: VersionValue | None
    facts_fingerprint: str

class CandidateFilter(DTO):
    mode: Literal['general','category','theme']
    category: Literal['quick_redeem','bank_wealth','bond_fund','mixed_fund','equity_fund'] | None = None
    bucket: Literal['cash_management','stable','growth'] | None = None
    theme_key: str | None = None
    theme_keys: list[str] = Field(default_factory=list)
    theme_operator: Literal['AND','OR'] | None = None

    @model_validator(mode='after')
    def validate_filter(self) -> Self:
        if self.mode=='theme':
            if self.category or bool(self.theme_key)==bool(self.theme_keys):raise ValueError('one explicit theme form required')
            keys=[self.theme_key] if self.theme_key else self.theme_keys
            if any(not k or not k.strip() or k!=k.strip() for k in keys) or len(set(keys))!=len(keys):raise ValueError('nonblank distinct themes required')
            if self.theme_keys and self.theme_operator is None:raise ValueError('multiple themes require explicit AND/OR')
            if self.theme_key and self.theme_operator:raise ValueError('operator only applies to theme_keys')
        else:
            if self.theme_keys or self.theme_operator:raise ValueError('theme fields outside theme mode')
            RecommendationFilter.model_validate({'mode':self.mode,'category':self.category,'theme_key':self.theme_key,'bucket':self.bucket})
        return self

class ShareReference(DTO):
    product_id: str
    product_version: VersionValue
    disclosure_version: VersionValue
    rules_version: VersionValue
    share_class: str | None
    target_ratio_total: str = '0'
    _ratio=field_validator('target_ratio_total')(ratio)

class QualificationDecision(DTO):
    product_id: str
    source_kind: SourceKind | None
    purchase_eligible: bool
    reason_codes: list[str]
    user_level: int | None
    product_level: int | None
    assessment_reasons: list[str]
    context: VersionContext
    checked_at: datetime
    evidence: dict
    holding_allowed: bool | None
    redemption_allowed: bool | None
    amount_executable: None = None
    amount_check_status: Literal['not_checked']='not_checked'

class ThemeEvidence(DTO):
    tag: str
    rule_version: str
    product_id: str
    share_class: str | None
    source_kind: SourceKind
    field: str
    excerpt: str
    source: str
    date: str
    reference_sha256: str
    mapping_evidence: dict | None = None

class FamilyCandidate(DTO):
    family_id: str
    default_share: ShareReference
    shares: list[QualificationDecision]
    matching_codes: list[str]
    member_codes: list[str]
    bucket: str
    data_version: str | None = None
    substantive_category: str | None = None
    issuer_identity: str | None
    text_evidence: dict

class PipelineResult(DTO):
    status: CandidateStatus
    reason_codes: list[str]
    stages: dict[str,int]
    matched_codes: list[str]
    decisions: list[QualificationDecision]
    tags: dict[str,list[ThemeEvidence]]
    exclusions: dict[str,list[str]]
    family_candidates: list[FamilyCandidate]
    pool_version: str
    context: VersionContext
    risk_explanation: dict | None = None

class SelectionDecision(DTO):
    accepted: bool
    reason_codes: list[str]
    decisions: list[QualificationDecision]
    failures: dict[str,list[str]]
    context: VersionContext

class SelectionRequest(DTO):
    expected_context: VersionContext
    items: list[ShareReference] = Field(min_length=1)

class SessionAction(DTO):
    session_id: str
    expected_version: Annotated[int,Field(strict=True,ge=1)]

class NextBatchRequest(SessionAction):
    cursor: str

class SessionSelectionRequest(SessionAction):
    items: list[ShareReference] = Field(min_length=1)

class SwitchRequest(SessionAction):
    family_id: str
    target: ShareReference
