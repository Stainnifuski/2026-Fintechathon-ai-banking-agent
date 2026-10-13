"""01正式计算provider，独立调用，不注册中央应用、不写数据库。"""
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal,localcontext
from fractions import Fraction
from typing import Sequence
from ...schemas.wealth_portfolio import (PortfolioPlan,PortfolioInput,CandidateGroup,ProductCandidate,ShareClass,
    Performance,PerformancePoint,Fees,RecommendationFilter,BucketTarget,SelectionItem)
from ...schemas.wealth_portfolio_candidates import CurrentFacts,CandidateFilter,ShareReference,SelectionRequest
from ...schemas.wealth_portfolio_data import AssetModelFacts
from ...schemas.wealth_portfolio_model import ModelResult
from ...domain.wealth_portfolio_candidates import ProductIndex,pipeline,reliable_family
from ...domain.wealth_portfolio_eligibility import qualify_purchase,assessment_reasons,version_context
from ...domain.wealth_portfolio_dpp import DppSelector,FEATURE_VERSION,ALGORITHM_VERSION
from ...domain.wealth_portfolio_allocation import (allocate,problem,validate_adjustment,integer_valid,BUCKETS,ModelError)
from ...domain.wealth_portfolio_projection import WeightFamily,WeightPart,project_products,validate_manual_products
from .wealth_portfolio_candidates import CandidateService,CandidateError
from .wealth_portfolio_data import read_asset_model_facts,digest

PROVIDER_VERSION='portfolio-provider-dpp-geometric-v1'


def ratio_strings(amounts: Sequence[int],total: int) -> list[str]:
    with localcontext() as ctx:
        ctx.prec=28;c=Decimal(amounts[0])/total;g=Decimal(amounts[2])/total;s=1-c-g
        return [str(c),str(s),str(g)]


def adapt_candidate_group(result: dict,index: ProductIndex,filter: CandidateFilter) -> CandidateGroup:
    """精确份额在证据保留；旧协议其他份额输出null，不伪装A/C或风险。"""
    details=result['pipeline'];status=result['status'];items=[]
    unknown_codes={'PRODUCT_RISK_UNKNOWN','PRODUCT_RISK_CONFLICT','FAMILY_UNVERIFIED','ISSUER_SOURCE_DATE_MISSING',
        'PRODUCT_VERSION_UNKNOWN','DISCLOSURE_VERSION_UNKNOWN','INVESTMENT_TEXT_SOURCE_MISSING','CURRENT_BANK_PRODUCT_UNAVAILABLE',
        'CURRENT_RULES_INCOMPLETE','PRODUCT_RISK_TIME_UNPROVEN','PRODUCT_SHARE_UNKNOWN'}
    all_reasons=set(details['reason_codes'])|{r for rs in details['exclusions'].values() for r in rs}
    if status=='no_eligible_products' and all_reasons & unknown_codes:status='unavailable'
    for family in result.get('items',[]):
        shares=[]
        for decision in family['shares']:
            p=index.products[decision['product_id']]
            if type(p.product_version) is not int or type(p.disclosure_version) is not int:status='unavailable';continue
            point=PerformancePoint(metric='nav_change',value_percent=None,status='unavailable',reason='未核实本次行情窗口',source=p.source_kind)
            offer=decision['evidence'].get('bank_fact',{})
            shares.append(ShareClass(product_id=p.product_id,product_version=p.product_version,disclosure_version=p.disclosure_version,
                share_class=p.share_class if p.share_class in ('A','C') else None,product_risk_level=decision['product_level'],
                status='purchase_eligible' if decision['purchase_eligible'] else 'purchase_ineligible',tradable=decision['purchase_eligible'],
                min_amount_fen=offer.get('minimum_fen'),fees=Fees(purchase_fee_rate=None,sales_service_fee_rate=None,redemption_fee_rules=None,missing_fields=['fee_rules']),
                performance=Performance(one_year=point,latest_day=point.model_copy(update={'metric':'daily_nav_change'})),detail_ref='local:'+p.product_id,data_source=p.source_kind,data_as_of=decision['checked_at']))
        if shares:items.append(ProductCandidate(family_id=family['family_id'],name=index.products[family['default_share']['product_id']].name,
            bucket=family['bucket'],reason='具体份额资格已核实；依据投资资料特征的DPP贪心选择。',reason_codes=['QUALIFIED_SHARE','DOCUMENT_FEATURE_DIVERSITY'],
            evidence={'default_share':family['default_share'],'share_decisions':family['shares'],'source_kind':result['source_kind'],'fixture_only':result['fixture_only']},share_classes=shares))
    return CandidateGroup(bucket=filter.bucket,filter=RecommendationFilter.model_validate(filter.model_dump()),status=status,items=items,
        reason_codes=sorted(all_reasons | set(result.get('reason_codes',[]))) if status!='ok' else [],
        next_cursor=result.get('session',{}).get('cursor') if result.get('session') else None,
        risk_explanation=details['risk_explanation'] if status=='risk_mismatch' else None)


def snapshot_content(snapshot) -> dict:
    """读取时点不是资产版本；内容、业务日及所有证据日期仍参与比较。"""
    value=snapshot.model_dump(mode='json')
    value.pop('as_of',None)
    value['business_at']=snapshot.business_at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    return value


class PortfolioModelProvider:
    provider_id='portfolio-dpp-projection'
    version=PROVIDER_VERSION

    def __init__(self,candidates: CandidateService,asset_source: object | None=None) -> None:
        self.candidates=candidates;self.asset_source=asset_source
        if candidates.selector is None:candidates.selector=DppSelector()
        if not isinstance(candidates.selector,DppSelector):raise ModelError('FORMAL_DPP_SELECTOR_REQUIRED')

    async def recommend(self,user_id: str,filter: CandidateFilter) -> ModelResult:
        result=await self.candidates.candidates(user_id,filter)
        index=self.candidates.indexes.get(result['pipeline']['context']['data_version'])
        group=adapt_candidate_group(result,index,filter)
        return ModelResult(status='ok' if group.status=='ok' else 'unavailable',candidate_groups=[group],reason_codes=result['reason_codes'],
            metadata={'algorithm_version':ALGORITHM_VERSION,'feature_version':FEATURE_VERSION,'fixture_only':result['fixture_only'],
                      'pipeline_stages':result['pipeline']['stages'],'exclusions':result['pipeline']['exclusions'],
                      'selection':self.candidates.selector.last_result if group.status=='ok' else None})

    async def _facts(self,user_id: str) -> tuple:
        facts,index,now=await self.candidates.current(user_id)
        gate=assessment_reasons(facts,user_id,now)
        if gate:raise ModelError('FORMAL_ASSESSMENT_UNAVAILABLE',{'reason_codes':gate})
        asset=await read_asset_model_facts(self.asset_source,user_id,list(index.products.values()))
        # Real sequential reads finish after the first eligibility timestamp.
        # Recheck at actual completion; never backdate the bank observation.
        initial_context=version_context(facts)
        facts,index,now=await self.candidates.current(user_id)
        if version_context(facts)!=initial_context:
            raise ModelError('MODEL_FACTS_CHANGED')
        gate=assessment_reasons(facts,user_id,now)
        if gate:raise ModelError('FORMAL_ASSESSMENT_UNAVAILABLE',{'reason_codes':gate})
        s=asset.snapshot
        if (s.basis_status=='fixture_only')!=(facts.mode=='fixture'):raise ModelError('ASSET_FACT_SOURCE_MISMATCH')
        provenance=asset.assessment_provenance
        if s.as_of>now or provenance.get('source')!=facts.source_id or provenance.get('version')!=facts.assessment.version:
            raise ModelError('ASSET_ASSESSMENT_CONTEXT_MISMATCH')
        if not s.assessment or (s.assessment.assessment_id,s.assessment.capacity_level,s.assessment.confirmed,s.assessment.purchase_allowed,s.assessment.expires_at)!=(facts.assessment.assessment_id,facts.assessment.capacity_level,facts.assessment.confirmed,facts.assessment.purchase_allowed,facts.assessment.expires_at):
            raise ModelError('ASSET_ASSESSMENT_CONTEXT_MISMATCH')
        for key in ('data_version','daily_version','offers_version'):
            if s.data_versions.get(key)!=getattr(facts,key):raise ModelError('ASSET_PRODUCT_CONTEXT_MISMATCH',{'field':key})
        return facts,index,now,asset

    def _families(self,asset: AssetModelFacts,facts: CurrentFacts,index: ProductIndex,now: datetime,
                  selected: Sequence[ShareReference] | None) -> tuple:
        s=asset.snapshot;T=s.total_assets_fen;current={};held={};missing=[];positions=[]
        for position in s.positions:
            p=index.products.get(position.product_id);family,reasons=reliable_family(p,now) if p else (None,['POSITION_PRODUCT_MISSING'])
            if position.market_value_fen is None or not family or not p or position.product_version!=p.product_version:
                missing.append('position_family_or_valuation:'+position.position_key)
                positions.append({'position':position.model_dump(mode='json'),'family_id':family,'target_fen':None,'reason_codes':reasons});continue
            current[family]=current.get(family,0)+position.market_value_fen
            held.setdefault(position.product_id,[]).append(position)
            positions.append({'position':position.model_dump(mode='json'),'family_id':family})
        eligible=pipeline(index,CandidateFilter(mode='general'),facts,facts.user_id,now)
        # 不把无法匹配的既有持仓当0；可给板块基准，但产品投影必须关闭并列出缺口。
        requested=list(selected) if selected is not None else []
        if selected is None:
            for bucket in BUCKETS:
                pool=[f for f in eligible.family_candidates if f.bucket==bucket]
                ids=self.candidates.selector.select(pool,set(),3)
                requested.extend(next(f.default_share for f in pool if f.family_id==i) for i in ids)
        if requested:
            decision=self.candidates.validate_current(SelectionRequest(expected_context=version_context(facts),items=requested),facts,index,facts.user_id,now)
            if not decision.accepted:raise ModelError('NEW_SELECTION_INELIGIBLE',decision.model_dump(mode='json'))
        by_bucket={b:{} for b in BUCKETS};part_seen=set()
        for ref in requested:
            p=index.products[ref.product_id];family,_=reliable_family(p,now)
            if family not in current and missing:missing.append('selected_family_current_value_unproven:'+family)
            f=WeightFamily(family,p.bucket,current.get(family,0),[])
            by_bucket[p.bucket][family]=f
            known=sum(x.market_value_fen for x in held.get(p.product_id,[]))
            q=qualify_purchase(p,facts,facts.user_id,now,ref)
            f.parts.append(WeightPart(p.product_id,known,True,q.holding_allowed is True,{'reference':ref.model_dump(mode='json'),'qualification':q.model_dump(mode='json'),'positions':[x.model_dump(mode='json') for x in held.get(p.product_id,[])]}));part_seen.add(p.product_id)
        for code,lots in held.items():
            p=index.products[code];family,_=reliable_family(p,now);q=qualify_purchase(p,facts,facts.user_id,now)
            if p.bucket is None:
                missing.append('position_bucket:'+code);continue
            # 既有维持必须明确可信允许，不因增购失败自动禁止持有或伪造允许。
            if q.holding_allowed is not True:continue
            f=by_bucket[p.bucket].setdefault(family,WeightFamily(family,p.bucket,current[family],[]))
            if code not in part_seen:f.parts.append(WeightPart(code,sum(x.market_value_fen for x in lots),False,True,{'qualification':q.model_dump(mode='json'),'positions':[x.model_dump(mode='json') for x in lots]}));part_seen.add(code)
        # 保留不同具体份额当前值，未许可持有部分不进入有效目标。
        for bucket,families in by_bucket.items():
            for family in families.values():family.current_fen=sum(p.current_fen for p in family.parts)
        cash=sum(a.balance_fen for a in s.accounts)+(s.receivable_fen or 0)
        by_bucket['cash_management']['__cash_asset__']=WeightFamily('__cash_asset__','cash_management',cash,[],True)
        return by_bucket,eligible,positions,missing

    async def plan(self,user_id: str,p: str | None=None,selected: Sequence[ShareReference] | None=None) -> ModelResult:
        facts,index,now,asset=await self._facts(user_id);s=asset.snapshot;T=s.total_assets_fen
        if not s.valuation_complete:return ModelResult(status='unavailable',reason_codes=['VALUATION_INCOMPLETE'],missing_fields=s.missing_fields)
        if T==0:return ModelResult(status='no_configurable_assets',reason_codes=['NO_CONFIGURABLE_ASSETS'])
        families,eligible,positions,missing=self._families(asset,facts,index,now,selected)
        h=None
        if not asset.disable_eta:
            values=[sum(a.balance_fen for a in s.accounts)+s.receivable_fen,0,0]
            for pos in s.positions:values[BUCKETS.index(index.products[pos.product_id].bucket)]+=pos.market_value_fen
            h=[v/T for v in values]
        available=[];caps=[]
        for bucket in BUCKETS:
            pool=[f for f in eligible.family_candidates if f.bucket==bucket];available.append(bool(pool) or bucket=='cash_management')
            cap=sum(f.cap_fen or 0 for f in families[bucket].values() if not f.unlimited)
            caps.append(Fraction(cap,T))
        model=allocate(facts.assessment.capacity_level,T,h,available,caps,p)
        outputs={};product_missing=list(missing)
        if not missing:
            for i,bucket in enumerate(BUCKETS):
                try:outputs[bucket]=project_products(sorted(families[bucket].values(),key=lambda f:f.family_id),model['amounts_fen'][i],T)
                except ModelError as error:product_missing.append(bucket+':'+error.code)
        cash_target=None;selections=[];target_by_product={}
        for bucket,output in outputs.items():
            for target in output['targets']:
                if target['cash']:cash_target=target['target_fen']
                for part in target['parts']:
                    product=index.products[part['product_id']];target_by_product[product.product_id]=part['target_fen']
                    if type(product.product_version) is not int:product_missing.append('product_version_protocol:'+product.product_id);continue
                    # 建议集合不同于用户已确认集合，未给selected时selected_positions为空。
                    if selected is not None and any(r.product_id==product.product_id for r in selected):
                        selections.append(SelectionItem(product_id=product.product_id,product_version=product.product_version,share_class=product.share_class if product.share_class in ('A','C') else None,target_ratio_total=str(Decimal(part['target_fen'])/T)))
        for row in positions:
            code=row['position']['product_id'];row['target_fen']=target_by_product.get(code,0) if not missing else None
            row['direction']='not_executed_target' if row['target_fen'] is not None else 'unknown_target'
        # 纯只读输出前再次验证可信当前版本、资产scope和快照指纹。
        fresh,index2,now2=await self.candidates.current(user_id)
        check=await read_asset_model_facts(self.asset_source,user_id,list(index2.products.values()))
        if version_context(fresh)!=version_context(facts) or snapshot_content(check.snapshot)!=snapshot_content(s) or assessment_reasons(fresh,user_id,now2):raise ModelError('MODEL_FACTS_CHANGED')
        ratios=ratio_strings(model['amounts_fen'],T);buckets=[]
        for i,b in enumerate(BUCKETS):
            lo,hi=model['ranges'][i];actual=float(ratios[i])
            if actual<lo-1e-10 or actual>hi+1e-10:raise ModelError('ROUNDED_TARGET_OUTSIDE_INTERVAL')
            buckets.append(BucketTarget(bucket=b,min_ratio=str(max(0.,min(lo,actual))),target_ratio=ratios[i],max_ratio=str(min(1.,max(hi,actual))),target_fen=model['amounts_fen'][i],current_fen=round(h[i]*T) if h else None,reason='固定政策、偏好参考与当前持仓的凸投影；区间属于同一目标损失预算。'))
        group_results=[]
        # 整体建议已在本次固定事实上运行DPP；不借会话多次读取混合版本。
        for bucket in BUCKETS:
            r=pipeline(index,CandidateFilter(mode='general',bucket=bucket),facts,user_id,now);pool=r.family_candidates
            ids=self.candidates.selector.select(pool,set(),3) if pool else []
            raw={'pipeline':r.model_dump(mode='json'),'status':r.status,'source_kind':facts.mode,'fixture_only':facts.mode=='fixture','items':[f.model_dump(mode='json') for f in pool if f.family_id in ids]}
            group_results.append(adapt_candidate_group(raw,index,CandidateFilter(mode='general',bucket=bucket)))
        plan=None
        if not product_missing and cash_target is not None:
            plan=PortfolioPlan(schema_version='wealth-portfolio-plan-v1',portfolio_id='calculation-'+digest({'snapshot':s.snapshot_id,'context':version_context(facts).model_dump(mode='json'),'model':model,'products':outputs})[:24],version=1,snapshot_id=s.snapshot_id,asset_scope_id=s.asset_scope_id,context_id=s.context_id,as_of=now,basis_kind=s.basis_kind,basis_status=s.basis_status,basis_fen=T,provider_id=self.provider_id,provider_version=self.version,model_status='fixture' if facts.mode=='fixture' else 'researched_model',buckets=buckets,
                growth_ratio_ex_cash=None if model['actual_p'] is None else str(model['actual_p']),candidate_groups=group_results,selected_positions=selections,cash_residual_fen=cash_target,
                data_versions={**s.data_versions,'assessment':facts.assessment.version,'context':version_context(facts).model_dump(mode='json')},missing_fields=[],reasons=['DOCUMENT_DPP_GREEDY','CONVEX_POLICY_PROJECTION','NOT_A_TRADE','FINANCIAL_EFFECT_NOT_EVALUATED'])
        contract_input=PortfolioInput(schema_version='wealth-portfolio-input-v1',snapshot=s,products=[item for g in group_results for item in g.items],features={'feature_version':FEATURE_VERSION},user_preferences={'growth_ratio_ex_cash':p})
        return ModelResult(status='partial' if product_missing else 'ok',plan=plan,candidate_groups=group_results,missing_fields=sorted(set(product_missing)),metadata={'model':model,'validation_contract':{'calculation':model,'qualification_context':version_context(facts).model_dump(mode='json'),'snapshot_id':s.snapshot_id,'selected_references':[part.evidence['reference'] for group in families.values() for family in group.values() for part in family.parts if part.purchase_eligible and 'reference' in part.evidence],'available':available,'caps':[str(c) for c in caps],'p':p},'product_projections':outputs,'positions':positions,'fixture_only':facts.mode=='fixture','input_schema':contract_input.schema_version,'snapshot':s.model_dump(mode='json'),'qualification_context':version_context(facts).model_dump(mode='json'),'model_input':contract_input.model_dump(mode='json'),'total_assets_fen':T,'available_cash_fen':sum(a.available_fen for a in s.accounts),'receivable_not_payable_fen':s.receivable_fen,'eta_disabled_reasons':asset.disable_eta_reasons,'financial_effect_status':'not_evaluated'})

    def validate_weights(self,user_id: str,facts: CurrentFacts,index: ProductIndex,now: datetime,asset: AssetModelFacts,
                         model: dict,targets: dict[str,int],bucket_amounts: Sequence[int]) -> dict:
        """纯手动入口：原U与全部份额/维持上限复验，合法金额原样返回。"""
        if not asset.snapshot.valuation_complete:raise ModelError('VALUATION_INCOMPLETE')
        if facts.user_id!=user_id or assessment_reasons(facts,user_id,now):raise ModelError('FORMAL_ASSESSMENT_UNAVAILABLE')
        if model.get('qualification_context')!=version_context(facts).model_dump(mode='json') or model.get('snapshot_id')!=asset.snapshot.snapshot_id:raise ModelError('MODEL_FACTS_CHANGED')
        refs=[ShareReference.model_validate(x) for x in model['selected_references']]
        families,_,_,missing=self._families(asset,facts,index,now,refs)
        if missing:raise ModelError('HOLDING_IDENTITY_INCOMPLETE',{'missing':missing})
        if len(bucket_amounts)!=3 or any(type(a) is not int or a<0 for a in bucket_amounts):return {'accepted':False,'reason_codes':['MANUAL_AMOUNT_INVALID'],'retained_targets_fen':None,'retained_bucket_amounts_fen':None}
        T=asset.snapshot.total_assets_fen
        if T<=0:raise ModelError('NO_CONFIGURABLE_ASSETS')
        meta=model['calculation'];P=problem(facts.assessment.capacity_level,meta['h'],model['available'],model['caps'],model.get('p'))
        check=validate_adjustment(P,meta['continuous_solution'],[a/T for a in bucket_amounts]);reasons=check['reason_codes'][:]
        if not integer_valid(P,bucket_amounts,T,request=False):reasons.append('MANUAL_INTEGER_POLICY_INVALID')
        all_keys={f.family_id for group in families.values() for f in group.values()}
        if set(targets)!=all_keys:reasons.append('MANUAL_PRODUCT_SET_MISMATCH')
        for i,b in enumerate(BUCKETS):
            fs=list(families[b].values());d=validate_manual_products(fs,{f.family_id:targets.get(f.family_id,0) for f in fs},bucket_amounts[i],T);reasons.extend(d['reason_codes'])
        return {'accepted':not reasons,'reason_codes':sorted(set(reasons)),'retained_targets_fen':dict(targets) if not reasons else None,'retained_bucket_amounts_fen':list(bucket_amounts) if not reasons else None}




