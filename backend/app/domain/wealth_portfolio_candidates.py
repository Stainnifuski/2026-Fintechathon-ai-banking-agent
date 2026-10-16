"""结构化匹配、证据标签、可靠家族及排序资料；不排序，不计算模型分数。"""
import re
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import ConfigDict
from ..schemas.wealth_portfolio_candidates import (CandidateFilter,ThemeEvidence,FamilyCandidate,ShareReference,
    PipelineResult,CurrentFacts)
from ..schemas.wealth_portfolio_data import ProductFact, DataSnapshot,FieldFact
from .wealth_portfolio_eligibility import qualify_purchase,assessment_reasons,version_context
from ..services.orchestration.wealth_portfolio_data import digest

class _ReadOnlyEmptyList(list):
    def _deny(self,*args,**kwargs):raise TypeError('immutable source observation')
    append=extend=insert=remove=pop=clear=sort=reverse=__setitem__=__delitem__=__iadd__=__imul__=_deny

class _ImmutableMissingField(FieldFact):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,frozen=True)

def intern_missing_fields(products):
    """Share identical proven-unknown constants, never evidence or user facts."""
    constants={};reused=0
    for p in products:
        for key,fact in p.fields.items():
            if fact.status!='missing' or fact.value is not None or fact.observations:continue
            signature=fact.reason
            if signature not in constants:
                frozen=_ImmutableMissingField.model_validate(fact.model_dump())
                object.__setattr__(frozen,'observations',_ReadOnlyEmptyList())
                constants[signature]=frozen
            else:reused+=1
            p.fields[key]=constants[signature]
    return reused

THEME_RULE_VERSION='portfolio-themes-v1'
THEME_RULES={
 'high_dividend':{'definition':'本产品明确投资于高股息股票或以高股息收益为目标','terms':['高股息','高分红收益']},
 'bond_income':{'definition':'本产品明确以债券票息收入为投资目标或策略','terms':['债券票息','债券利息收入']},
 'broad_market':{'definition':'明确标的且标的已由核实映射标注宽基','terms':[]},
}


def sourced_value(product: ProductFact, field: str) -> tuple[str | None,list]:
    fact=product.fields.get(field)
    if not fact or fact.status in ('missing','conflict') or not isinstance(fact.value,str) or not fact.value.strip():return None,[]
    observations=[o for o in fact.observations if o.value==fact.value and o.source and o.field and o.reference.sha256
        and (o.as_of is not None or o.observed_at is not None)]
    return (fact.value,observations) if observations else (None,[])


def issuer_identity(product: ProductFact) -> tuple[str | None,str | None]:
    raw=product.fields.get('issuer_raw')
    # 不冲突的单一公司原文即可用；仅合并不同名称才要求别名证据。
    if raw and raw.status=='conflict':return None,'ISSUER_CONFLICT'
    normalized,_=sourced_value(product,'issuer_normalized')
    original,_=sourced_value(product,'issuer_raw')
    if normalized:return normalized,None
    if original:return original,None
    return None,'ISSUER_SOURCE_DATE_MISSING'


def theme_tags(product: ProductFact, mappings: dict | None=None) -> list[ThemeEvidence]:
    output=[];mappings=mappings or {}
    for field in ('objective','strategy'):
        text,observations=sourced_value(product,field)
        if not text:continue
        for clause in re.split(r'[。；;\n！？]',text):
            # 否定/旁引不确定时关闭，不用LLM或名称猜本产品策略。
            if re.search(r'不|非|避免|未采用|其他基金|别的基金|相比|相较|例如|举例',clause):continue
            if not re.search(r'本基金|本产品|投资于|重点投资|主要投资|配置|为目标|力争|着重投资|获取',clause):continue
            for tag,rule in THEME_RULES.items():
                if any(term in clause for term in rule['terms']):
                    o=observations[0];output.append(ThemeEvidence(tag=tag,rule_version=THEME_RULE_VERSION,
                        product_id=product.product_id,share_class=product.share_class,source_kind=product.source_kind,
                        field=field,excerpt=clause.strip()[:400],source=o.source,date=str(o.as_of or o.observed_at.date()),reference_sha256=o.reference.sha256))
    for field in ('tracking_target','benchmark'):
        text,observations=sourced_value(product,field)
        mapping=mappings.get(text)
        if not mapping or mapping.get('source_kind')!=product.source_kind or not mapping.get('verified') or not mapping.get('source') or not mapping.get('date') or not mapping.get('sha256'):continue
        # 精确、核实映射，不从指数名字相似猜跟踪关系。
        for tag in mapping.get('tags',[]):
            if tag not in THEME_RULES:continue
            o=observations[0];output.append(ThemeEvidence(tag=tag,rule_version=THEME_RULE_VERSION,product_id=product.product_id,
                share_class=product.share_class,source_kind=product.source_kind,field=field,excerpt=text[:400],source=o.source,
                date=str(o.as_of or o.observed_at.date()),reference_sha256=o.reference.sha256,mapping_evidence=mapping))
    return output


def reliable_family(product: ProductFact, now: datetime) -> tuple[str | None,list[str]]:
    business_day=now.astimezone(ZoneInfo('Asia/Shanghai')).date()
    records=[e for e in product.family_evidence if e.kind=='family' and (e.published_on is None or e.published_on<=business_day)
        and (e.applicable_from is None or e.applicable_from<=business_day) and (e.applicable_to is None or business_day<=e.applicable_to) and e.source_kind==product.source_kind and product.product_id in e.codes
        and e.share_classes.get(product.product_id)==product.share_class and e.observed_at<=now]
    field=product.fields.get('family_id')
    if not records and product.source_kind=='simulated' and product.current_binding:
        identities=[e for e in product.identity_evidence if e.kind=='entity' and e.source_kind=='simulated'
            and e.source_role=='simulation' and e.codes==[product.product_id] and e.observed_at<=now
            and e.share_classes[product.product_id]==product.share_class and e.family_id=='bank-sku:'+product.product_id]
        if identities:return 'bank-sku:'+product.product_id,[]
    if not records:
        if (product.source_kind=='simulated' and product.scope_status=='verified'
                and type(product.product_version) is int and type(product.disclosure_version) is int
                and any(r.function=='simulation_config' and r.sha256 for r in product.references)):
            # A frozen explicit SKU is an operational identity. This does not
            # invent a multi-code fund family or independent financial risk.
            # Current bank identity, grade, rules and scope are still qualified.
            return 'exact-simulation-code:'+product.product_id,[]
        if product.source_kind=='real' and product.execution_policy and product.scope_status=='verified':
            # Operational identity only. This does not assert a fund family
            # or independent financial risk when a relationship is unknown.
            return 'exact-code:'+product.product_id,[]
        return None,['FAMILY_UNVERIFIED']
    values={e.family_id for e in records}
    if len(values)!=1 or (field and field.status=='conflict'):return None,['FAMILY_CONFLICT']
    return next(iter(values)),[]

class ProductIndex:
    """每个资料版本建立一次只读索引，保留全目录台账和标签出处。"""
    def __init__(self,snapshot: DataSnapshot,mappings: dict | None=None,*,immutable_source: bool=False):
        self.version=snapshot.version;self.daily_version=snapshot.daily_version
        self.products={p.product_id:p for p in snapshot.products}
        if len(self.products)!=len(snapshot.products):raise ValueError('DUPLICATE_PRODUCT_IDENTITY')
        self.interned_unknown_fields=intern_missing_fields(snapshot.products) if immutable_source else 0
        # This index is a version-bound immutable source, not a user result.
        # Hash its material once instead of serializing the full directory at
        # every lifecycle read. New material must load a new index/version.
        self._content_fingerprint=digest([p.model_dump(mode='json') for p in snapshot.products]) if immutable_source else None
        self.tags={p.product_id:theme_tags(p,mappings) for p in snapshot.products}
        self.scope_codes=[p.product_id for p in snapshot.products if p.scope_status=='verified']
        self.family_members=defaultdict(list)
        for p in snapshot.products:
            families={e.family_id for e in p.family_evidence if e.source_kind==p.source_kind}
            identity,_=reliable_family(p,snapshot.as_of)
            if identity:families.add(identity)
            for family in families:
                self.family_members[family].append(p)
        self.directory_counts=dict(Counter(p.scope_status for p in snapshot.products))
        from .wealth_paper_policy import policy_sha
        self.policy_references={p.product_id:(policy_sha(p.execution_policy),p.execution_policy) for p in snapshot.products if p.execution_policy} if immutable_source else {}

    @property
    def content_fingerprint(self):
        # Injected/live in-memory sources may mutate; do not memoize those.
        return self._content_fingerprint or digest([p.model_dump(mode='json') for p in self.products.values()])

    def match(self,filter: CandidateFilter) -> list[ProductFact]:
        keys=[filter.theme_key] if filter.theme_key else filter.theme_keys
        if any(k not in THEME_RULES for k in keys):raise ValueError('UNKNOWN_THEME_KEY')
        result=[]
        for code in self.scope_codes:
            p=self.products[code]
            if filter.bucket and p.bucket!=filter.bucket:continue
            if filter.mode=='category':
                expected={'bond_fund':{'bond'},'mixed_fund':{'mixed'},'equity_fund':{'equity'}}
                bank=p.source_kind=='simulated' and (p.product_id.startswith('B') or p.forms.get('kind')=='bank_wealth')
                if filter.category=='bank_wealth':
                    if not bank:continue
                elif filter.category=='quick_redeem':
                    # 政策活钱银行必须在第02步已核实，pending不能进池。
                    if not bank or p.bucket!='cash_management':continue
                elif p.substantive_category not in expected[filter.category] or bank:continue
            if filter.mode=='theme':
                present={t.tag for t in self.tags[code]}
                if (all(k in present for k in keys) if filter.theme_operator!='OR' else any(k in present for k in keys)) is False:continue
            result.append(p)
        return result


def family_candidates(index: ProductIndex,matched: list[ProductFact],decisions: list, facts: CurrentFacts,now: datetime) -> tuple[list[FamilyCandidate],dict[str,list[str]],int]:
    qualified={d.product_id:d for d in decisions if d.purchase_eligible};all_decisions={d.product_id:d for d in decisions}
    groups=defaultdict(list);exclusions={};family_verified_count=0
    families_by_code={p.product_id:reliable_family(p,now) for p in matched}
    matched_by_family=defaultdict(list)
    for p in matched:
        family,reasons=families_by_code[p.product_id]
        if family:matched_by_family[family].append(p)
        if p.product_id not in qualified:continue
        if reasons:exclusions[p.product_id]=reasons;continue
        groups[family].append(p);family_verified_count+=1
    candidates=[]
    for family,shares in groups.items():
        members=[p for p in index.family_members[family] if reliable_family(p,now)[0]==family]
        # 可靠关系的类别冲突仍阻断；额外主体／正文不是通用门槛。
        conflicts=[]
        if len({p.substantive_category for p in members if p.substantive_category})>1:conflicts.append('FAMILY_CATEGORY_CONFLICT')
        # Optional issuer/body evidence does not gate code-based selection.
        enough=[];details={}
        for p in shares:
            reasons=list(conflicts);issuer,problem=issuer_identity(p)
            if not p.substantive_category or p.bucket is None:reasons.append('SUBSTANTIVE_CATEGORY_UNPROVEN')
            texts={field:sourced_value(p,field)[0] for field in ('objective','strategy','benchmark','tracking_target')}
            if reasons:exclusions[p.product_id]=list(dict.fromkeys(reasons))
            else:enough.append(p);details[p.product_id]={'issuer':issuer,'texts':texts}
        if not enough:continue
        # Known related codes deduplicate; no A/C preference or letter inference.
        default=min(enough,key=lambda p:p.product_id)
        offer=facts.offers[default.product_id]
        candidates.append(FamilyCandidate(family_id=family,default_share=ShareReference(product_id=default.product_id,
            product_version=default.product_version,disclosure_version=default.disclosure_version,share_class=default.share_class,rules_version=offer.rules_version),
            shares=[all_decisions[p.product_id] for p in matched_by_family[family]],matching_codes=[p.product_id for p in shares],
            member_codes=[p.product_id for p in members],bucket=default.bucket,data_version=index.version,substantive_category=default.substantive_category,issuer_identity=details[default.product_id]['issuer'],text_evidence=details[default.product_id]['texts']))
    return candidates,exclusions,family_verified_count


def pipeline(index: ProductIndex,filter: CandidateFilter,facts: CurrentFacts,user_id: str,now: datetime,*,checked_decisions=None) -> PipelineResult:
    matched=index.match(filter)
    # 先完整匹配，再逐份额资格；所有匹配及排除明细都保留。
    context=version_context(facts)
    if checked_decisions is not None:
        decisions=[checked_decisions[p.product_id] for p in matched]
        if any(d.context!=context or d.checked_at!=now for d in decisions):raise ValueError('QUALIFICATION_CONTEXT_STALE')
    else:decisions=[qualify_purchase(p,facts,user_id,now,context=context) for p in matched]
    families,material_exclusions,family_verified_count=family_candidates(index,matched,decisions,facts,now)
    exclusions={d.product_id:d.reason_codes[:] for d in decisions if not d.purchase_eligible}
    for code,reasons in material_exclusions.items():exclusions.setdefault(code,[]).extend(reasons)
    gate=assessment_reasons(facts,user_id,now);risk_explanation=None
    if not matched:
        status={'theme':'no_theme_match','category':'no_category_match','general':'no_eligible_products'}[filter.mode];reasons=['NO_STRUCTURED_MATCH']+gate
    elif gate:status='unavailable';reasons=gate
    elif families:status='ok';reasons=[]
    elif all(d.product_level is not None and d.user_level is not None and d.product_level>d.user_level for d in decisions):
        status='risk_mismatch';reasons=['ALL_MATCHED_RISK_EXCEEDS_CAPACITY'];risk_explanation={'user_level':facts.assessment.capacity_level,'product_levels':sorted({d.product_level for d in decisions}),'other_reasons':sorted({r for rs in exclusions.values() for r in rs if r!='RISK_EXCEEDS_CAPACITY'})}
    else:status='no_eligible_products';reasons=sorted({r for rs in exclusions.values() for r in rs})
    # Observation timestamps are not pool revisions. Full decisions remain in
    # the response/audit; current identity/risk/rules/price are bound by context.
    # Serializing the nested current-bank read time made every real next-batch
    # request falsely stale despite unchanged products and user facts.
    pool_version=digest({'data':index.version,'context':version_context(facts).model_dump(mode='json'),
        'filter':filter.model_dump(mode='json'),'families':[f.model_dump(mode='json',exclude={'shares'}) for f in families]})
    return PipelineResult(status=status,reason_codes=list(dict.fromkeys(reasons)),stages={'directory':len(index.products),'scope_verified':len(index.scope_codes),
        'matched':len(matched),'purchase_qualified_shares':sum(d.purchase_eligible for d in decisions),'family_verified_qualified_shares':family_verified_count,
        'family_groups':len({reliable_family(index.products[d.product_id],now)[0] for d in decisions if d.purchase_eligible and reliable_family(index.products[d.product_id],now)[0]}),
        'material_complete_families':len(families)},matched_codes=[p.product_id for p in matched],decisions=decisions,
        tags={p.product_id:index.tags[p.product_id] for p in matched if index.tags[p.product_id]},exclusions=exclusions,family_candidates=families,
        pool_version=pool_version,context=version_context(facts),risk_explanation=risk_explanation)
