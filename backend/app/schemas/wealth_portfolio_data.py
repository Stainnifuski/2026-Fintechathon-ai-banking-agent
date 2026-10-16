"""只读模型事实：来源逐项绑定，未知不是资格或交易许可。"""
from datetime import date, datetime
from typing import Any, Literal, Self, Annotated
from pydantic import Field, model_validator
from .wealth_portfolio import DTO, Risk, AssetSnapshot
VersionValue=Annotated[int,Field(strict=True,ge=1)] | Annotated[str,Field(strict=True,min_length=1)]

class RawReference(DTO):
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    function: str
    kwargs: dict = Field(default_factory=dict)
    observed_at: datetime | None = None

class Observation(DTO):
    value: Any = None
    raw_value: Any = None
    source: str
    field: str
    reference: RawReference
    observed_at: datetime | None = None
    as_of: date | None = None
    unit: Literal['fen','yuan_per_share','yuan_per_10000_shares','percent','source_unit_unverified'] | None = None
    reason: str | None = None

    @model_validator(mode='after')
    def units(self) -> Self:
        from decimal import Decimal, InvalidOperation
        if self.value is None:return self
        if self.unit=='fen' and (type(self.value) is not int or self.value<0):raise ValueError('fen must be nonnegative strict integer')
        if self.unit in ('yuan_per_share','yuan_per_10000_shares','percent'):
            if not isinstance(self.value,str):raise ValueError('numeric metric must be decimal string')
            try:number=Decimal(self.value)
            except InvalidOperation as error:raise ValueError('invalid metric') from error
            if not number.is_finite():raise ValueError('nonfinite metric')
            if self.unit=='yuan_per_share' and number<=0:raise ValueError('NAV must be positive')
        return self

class FieldFact(DTO):
    value: Any = None
    status: Literal['observed','verified','missing','conflict','simulated']
    reason: str | None = None
    observations: list[Observation] = Field(default_factory=list)

    @model_validator(mode='after')
    def validity(self) -> Self:
        if self.status in ('observed','verified','simulated') and self.value is None:
            raise ValueError('present fact requires non-null value')
        if self.status in ('missing','conflict') and (self.value is not None or not self.reason):
            raise ValueError('unknown/conflict requires null and reason')
        return self

class EvidenceRecord(DTO):
    """正式登记须给出具体实体、出处、公开时间；适用时间未知仍显式保留。"""
    evidence_id: str
    source_kind: Literal['real','simulated','fixture']
    kind: Literal['family','risk','issuer','entity','category','objective','strategy','benchmark','tracking_target']
    source_role: Literal['issuer','seller','simulation','fixture']
    url: str
    codes: list[str]
    share_classes: dict[str,str | None]
    channel: str
    published_on: date | None
    applicable_from: date | None
    applicable_to: date | None
    applicability_reason: str | None
    observed_at: datetime
    reference: RawReference
    excerpt: str
    family_id: str | None = None
    risk_level: Risk | None = None
    issuer_name: str | None = None
    field_value: str | None = None
    location: str | None = None
    document_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def binding(self) -> Self:
        from urllib.parse import urlparse
        host=urlparse(self.url).hostname
        if not host or not self.channel.strip() or len(self.excerpt.strip())<8:
            raise ValueError('source/channel/excerpt required')
        if not self.codes or len(set(self.codes))!=len(self.codes) or set(self.share_classes)!=set(self.codes):
            raise ValueError('exact share binding required')
        if any(c not in self.excerpt for c in self.codes):
            raise ValueError('excerpt must explicitly identify each code')
        if self.source_kind=='real' and self.source_role not in ('issuer','seller'):
            raise ValueError('simulation cannot verify real facts')
        if self.applicable_from is None and not self.applicability_reason:
            raise ValueError('unknown applicability requires reason')
        if self.applicable_to and self.applicable_from and self.applicable_to<self.applicable_from:
            raise ValueError('invalid applicability interval')
        if self.kind=='family' and (not self.family_id or len(self.codes)<2):
            raise ValueError('family requires explicit relationship of multiple codes')
        if self.kind=='risk' and (self.risk_level is None or len(self.codes)!=1):
            raise ValueError('risk is per specific share/channel')
        if self.kind=='issuer' and not self.issuer_name:
            raise ValueError('issuer name required')
        if self.kind=='entity' and (len(self.codes)!=1 or not self.family_id):
            raise ValueError('single entity requires exact SKU identity')
        if self.kind in ('category','objective','strategy','benchmark','tracking_target') and not self.field_value:
            raise ValueError('document field value required')
        if self.published_on is None and not self.applicability_reason:
            raise ValueError('unknown publication requires explicit reason')
        if self.published_on is not None and self.published_on>self.observed_at.date():
            raise ValueError('future publication evidence')
        return self

class ProductFact(DTO):
    product_id: str
    source_kind: Literal['real','simulated','fixture']
    share_class: str | None = None
    product_version: VersionValue | None = None
    disclosure_version: VersionValue | None = None
    name: str | None = None
    raw_category: str | None
    substantive_category: str | None
    classification_status: str
    classification_basis: str | None
    forms: dict
    scope_status: Literal['verified','pending','excluded','policy_mismatch']
    scope_reason: str | None
    bucket: Literal['cash_management','stable','growth'] | None
    fields: dict[str,FieldFact]
    risk_evidence: list[EvidenceRecord] = Field(default_factory=list)
    family_evidence: list[EvidenceRecord] = Field(default_factory=list)
    identity_evidence: list[EvidenceRecord] = Field(default_factory=list)
    current_binding: dict | None = None
    execution_policy: dict | None = None # approved project policy, never issuer evidence
    references: list[RawReference] = Field(default_factory=list)
    policy_version: str = 'portfolio-scope-20261009-v1'
    approved_trade_product: Literal[False] = False

class DailyFact(DTO):
    product_id: str
    source_kind: Literal['real','simulated','fixture']
    fields: dict[str,FieldFact]
    observations_cutoff: datetime
    freshness: Literal['stale','unknown','observed']
    freshness_reason: str

class DataSnapshot(DTO):
    schema_version: Literal['wealth-portfolio-data-v1']='wealth-portfolio-data-v1'
    adapter_version: str
    version: str
    fixed_version: str
    daily_version: str
    evidence_version: str
    as_of: datetime
    products: list[ProductFact]
    daily: list[DailyFact]
    input_references: list[RawReference]
    coverage: dict
    external_requests: Literal[0] = 0

class AssetModelFacts(DTO):
    snapshot: AssetSnapshot
    classification_complete: bool
    disable_eta: bool
    disable_eta_reasons: list[str]
    assessment_provenance: dict
