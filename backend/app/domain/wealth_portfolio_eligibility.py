"""新购/增购唯一资格函数；不处理金额成交，不处置已有持仓。"""
from datetime import datetime
from zoneinfo import ZoneInfo
from ..schemas.wealth_portfolio_candidates import CurrentFacts, VersionContext, QualificationDecision, ShareReference
from ..schemas.wealth_portfolio_data import ProductFact
from ..services.orchestration.wealth_portfolio_data import digest

def current_offer_reasons(offer, facts: CurrentFacts, now: datetime) -> list[str]:
    """已给可信区间仍按区间检查；仅受保护当前模拟银行可走业务规则分支。"""
    has_window=offer.valid_from is not None or offer.valid_until is not None
    if has_window and not (offer.valid_from is not None and offer.valid_until is not None and offer.valid_from<=now<offer.valid_until):
        return ['BANK_FACT_TIME_UNPROVEN']
    evidence=offer.current_simulation
    if has_window and evidence is None:return []
    if (facts.mode!='simulated' or offer.source_kind!='simulated' or evidence is None
        or facts.source_id!='protected-local-bank' or offer.source_id!=facts.source_id
        or facts.channel!='local-competition-bank' or offer.channel!=facts.channel):
        return ['BANK_FACT_TIME_UNPROVEN']
    reasons=[]
    if ((evidence.product_id,evidence.product_version,evidence.disclosure_version)
        !=(offer.product_id,offer.product_version,offer.disclosure_version)):
        reasons.append('BANK_CURRENT_EVIDENCE_MISMATCH')
    if evidence.observed_at!=facts.read_at or facts.read_at!=now or evidence.business_at>evidence.observed_at:
        reasons.append('BANK_CURRENT_READ_TIME_UNPROVEN')
    if evidence.status!='on_sale' or evidence.purchase_paused or not evidence.purchase_open:
        reasons.append('PURCHASE_STOPPED')
    if evidence.data_stale:reasons.append('BANK_DATA_STALE')
    from .wealth import pricing_date,nav,advance_business
    try:
        day=evidence.business_at.astimezone(ZoneInfo('Asia/Shanghai')).date()
        priced=pricing_date(evidence.business_at,evidence.cutoff,evidence.calendar_version)
        if evidence.purchase_end_date and priced>evidence.purchase_end_date:reasons.append('BANK_PURCHASE_PERIOD_ENDED')
        from datetime import date
        if (priced-date(2026,9,30)).days%evidence.open_interval_days:reasons.append('BANK_NON_OPEN_DAY')
        if evidence.nav is None or evidence.nav_date is None or evidence.available_at is None:
            reasons.append('BANK_NAV_TIME_UNPROVEN')
        else:
            nav(evidence.nav)
            if evidence.nav_date>day or evidence.available_at>evidence.business_at:reasons.append('BANK_FUTURE_DATA')
            if advance_business(evidence.nav_date,3,evidence.calendar_version)<day:reasons.append('BANK_DATA_STALE')
    except (ValueError,ArithmeticError,KeyError):reasons.append('BANK_BUSINESS_RULES_UNPROVEN')
    return list(dict.fromkeys(reasons))

def offer_is_current(offer, facts: CurrentFacts, now: datetime) -> bool:
    return not current_offer_reasons(offer,facts,now)


def version_context(facts: CurrentFacts) -> VersionContext:
    value=facts.model_dump(mode='json');value.pop('read_at')
    if value['assessment']:value['assessment'].pop('read_at')
    for offer in value['offers'].values():
        current=offer.get('current_simulation')
        if current:
            current.pop('observed_at')
            current['business_at']=datetime.fromisoformat(current['business_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    return VersionContext(user_id=facts.user_id,source_id=facts.source_id,mode=facts.mode,channel=facts.channel,
        data_version=facts.data_version,daily_version=facts.daily_version,assessment_id=facts.assessment.assessment_id if facts.assessment else None,
        assessment_version=facts.assessment.version if facts.assessment else None,offers_version=facts.offers_version,
        facts_fingerprint=digest(value))


def assessment_reasons(facts: CurrentFacts, user_id: str, now: datetime) -> list[str]:
    reasons=[]
    if facts.user_id!=user_id:reasons.append('IDENTITY_MISMATCH')
    if not facts.source_id or not facts.channel:reasons.append('FACT_SOURCE_UNAVAILABLE')
    if facts.read_at!=now:reasons.append('FACT_READ_TIME_MISMATCH')
    assessment=facts.assessment
    if assessment is None:return reasons+['ASSESSMENT_REQUIRED']
    if assessment.user_id!=user_id:reasons.append('ASSESSMENT_IDENTITY_MISMATCH')
    if assessment.source_id!=facts.source_id or assessment.source_kind!=facts.mode:reasons.append('ASSESSMENT_SOURCE_MISMATCH')
    if assessment.read_at!=now:reasons.append('ASSESSMENT_READ_TIME_MISMATCH')
    if not assessment.assessment_id or assessment.version is None:reasons.append('ASSESSMENT_VERSION_UNKNOWN')
    if not facts.assessment_current_proven:reasons.append('ASSESSMENT_CURRENT_VERSION_UNPROVEN')
    if (assessment.assessment_id,assessment.version)!=(facts.latest_assessment_id,facts.latest_assessment_version):reasons.append('ASSESSMENT_VERSION_STALE')
    if type(assessment.capacity_level) is not int or not 1<=assessment.capacity_level<=5:reasons.append('ASSESSMENT_LEVEL_INVALID')
    if not assessment.confirmed:reasons.append('ASSESSMENT_UNCONFIRMED')
    if not assessment.purchase_allowed:reasons.append('ASSESSMENT_PURCHASE_FORBIDDEN')
    if assessment.expires_at is None:reasons.append('ASSESSMENT_EXPIRY_UNKNOWN')
    elif assessment.expires_at<=now:reasons.append('ASSESSMENT_EXPIRED')
    return list(dict.fromkeys(reasons))


def qualify_purchase(product: ProductFact | None, facts: CurrentFacts, user_id: str, now: datetime,
                     requested: ShareReference | None = None, context: VersionContext | None = None) -> QualificationDecision:
    """所有入口逐具体份额调用；权重不参与风险比较，0亦不能绕过。"""
    gate=assessment_reasons(facts,user_id,now);reasons=list(gate);product_level=None
    user_level=facts.assessment.capacity_level if not gate else None
    code=product.product_id if product else requested.product_id
    offer=facts.offers.get(code);hold=None;redeem=None;details={}
    if product is None:reasons.append('PRODUCT_NOT_FOUND')
    else:
        if product.scope_status!='verified':reasons.append({'excluded':'PRODUCT_OUT_OF_SCOPE','pending':'PRODUCT_SCOPE_UNPROVEN','policy_mismatch':'SIMULATED_POLICY_MISMATCH'}[product.scope_status])
        if product.source_kind!=facts.mode and facts.mode!='fixture':reasons.append('PRODUCT_SOURCE_KIND_MISMATCH')
        if product.source_kind=='fixture' and facts.mode!='fixture':reasons.append('FIXTURE_SOURCE_FORBIDDEN')
        if product.product_version is None:reasons.append('PRODUCT_VERSION_UNKNOWN')
        if product.disclosure_version is None:reasons.append('DISCLOSURE_VERSION_UNKNOWN')
        if product.source_kind=='real' and product.share_class is None:reasons.append('PRODUCT_SHARE_UNKNOWN')
        if requested:
            for attr in ('product_version','disclosure_version','share_class'):
                if getattr(requested,attr)!=getattr(product,attr):reasons.append('SELECTED_'+attr.upper()+'_MISMATCH')
        if product.source_kind=='real':
            scoped=[e for e in product.risk_evidence if e.source_kind=='real' and code in e.codes and e.share_classes.get(code)==product.share_class and e.channel==facts.channel]
            details['risk_evidence_ids']=[e.evidence_id for e in scoped]
            if not product.risk_evidence:reasons.append('PRODUCT_RISK_UNKNOWN')
            elif not scoped:reasons.append('PRODUCT_RISK_SHARE_CHANNEL_MISMATCH')
            else:
                business_day=now.astimezone(ZoneInfo('Asia/Shanghai')).date()
                active=[e for e in scoped if e.observed_at<=now and e.published_on is not None and e.published_on<=business_day
                    and e.applicable_from is not None and e.applicable_from<=business_day
                    and e.applicable_to is not None and business_day<=e.applicable_to]
                if len(active)!=len(scoped):reasons.append('PRODUCT_RISK_TIME_UNPROVEN')
                levels={e.risk_level for e in scoped}
                if len(levels)!=1:reasons.append('PRODUCT_RISK_CONFLICT')
                elif len(active)==len(scoped):product_level=next(iter(levels))
                if offer and offer.risk_evidence_id not in details['risk_evidence_ids']:reasons.append('CURRENT_RISK_EVIDENCE_MISMATCH')
        else:
            field=product.fields.get('formal_risk')
            if field is not None and field.status=='conflict':reasons.append('PRODUCT_RISK_CONFLICT')
            elif field is None or field.value is None:reasons.append('PRODUCT_RISK_UNKNOWN')
            elif (product.source_kind=='simulated' and field.status!='simulated') or type(field.value) is not int or not 1<=field.value<=5:reasons.append('PRODUCT_RISK_UNVERIFIED')
            else:product_level=field.value
        if offer is None:reasons.append('CURRENT_BANK_PRODUCT_UNAVAILABLE')
        else:
            if offer.product_id!=code or offer.share_class!=product.share_class:reasons.append('BANK_SHARE_IDENTITY_MISMATCH')
            if product.current_binding:
                bound=product.current_binding
                current=offer.current_simulation
                if (product.source_kind!='simulated' or bound.get('source')!='protected-local-bank-current-products'
                    or current is None or current.product_content_sha256!=bound.get('content_sha256')
                    or not bound.get('content_sha256')):
                    reasons.append('BANK_PRODUCT_CONTENT_MISMATCH')
            if offer.source_id!=facts.source_id or offer.source_kind!=facts.mode:reasons.append('BANK_FACT_SOURCE_MISMATCH')
            if offer.channel!=facts.channel:reasons.append('BANK_CHANNEL_MISMATCH')
            if offer.product_version is None or offer.product_version!=product.product_version:reasons.append('BANK_PRODUCT_VERSION_MISMATCH')
            if offer.disclosure_version is None or offer.disclosure_version!=product.disclosure_version:reasons.append('BANK_DISCLOSURE_VERSION_MISMATCH')
            if offer.rules_version is None:reasons.append('BANK_RULES_VERSION_UNKNOWN')
            if facts.offers_version is None:reasons.append('BANK_FACTS_VERSION_UNKNOWN')
            if requested and offer.rules_version!=requested.rules_version:reasons.append('SELECTED_RULES_VERSION_MISMATCH')
            time_reasons=current_offer_reasons(offer,facts,now)
            # 真实基金即使附带模拟来源描述，也不能绕过真实窗口要求。
            if product.source_kind!='simulated' and offer.valid_from is None and offer.valid_until is None:
                time_reasons=['BANK_FACT_TIME_UNPROVEN']
            current=not time_reasons
            if not current:reasons.extend(['BANK_FACT_TIME_UNPROVEN',*time_reasons])
            if offer.purchase_allowed is None:reasons.append('PURCHASE_STATUS_UNKNOWN')
            elif not offer.purchase_allowed:reasons.append('PURCHASE_STOPPED')
            if not offer.rules_complete:reasons.append('CURRENT_RULES_INCOMPLETE')
            if offer.minimum_fen is None:reasons.append('PURCHASE_MINIMUM_UNKNOWN')
            if offer.limit_kind=='unknown' or (offer.limit_kind=='bounded' and offer.maximum_fen is None):reasons.append('PURCHASE_LIMIT_UNKNOWN')
            if offer.maximum_fen is not None and offer.minimum_fen is not None and offer.maximum_fen<offer.minimum_fen:reasons.append('PURCHASE_LIMIT_CONFLICT')
            if not offer.risk_current_confirmed:reasons.append('CURRENT_RISK_UNPROVEN')
            if offer.risk_level!=product_level:reasons.append('CURRENT_RISK_CONFLICT')
            if current and offer.source_id==facts.source_id and offer.channel==facts.channel and offer.product_version==product.product_version and offer.disclosure_version==product.disclosure_version and offer.share_class==product.share_class and offer.product_id==code and offer.source_kind==facts.mode:
                hold=offer.holding_allowed;redeem=offer.redemption_allowed
            details['bank_fact']=offer.model_dump(mode='json')
        # 必须真实到达这条比较；失败的评估或未知产品等级不冒称已比较。
        if user_level is not None and product_level is not None and product_level>user_level:reasons.append('RISK_EXCEEDS_CAPACITY')
        details['product_version']=product.product_version;details['disclosure_version']=product.disclosure_version
        details['research_approved_trade_product']=product.approved_trade_product
    return QualificationDecision(product_id=code,source_kind=product.source_kind if product else None,purchase_eligible=not reasons,
        reason_codes=list(dict.fromkeys(reasons)),user_level=user_level,product_level=product_level,assessment_reasons=gate,
        context=context or version_context(facts),checked_at=now,evidence=details,holding_allowed=hold,redemption_allowed=redeem)
