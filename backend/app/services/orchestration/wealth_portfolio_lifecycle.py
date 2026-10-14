"""01方案生命周期handler；纯配置、服务端原契约和原子替换。"""
import secrets
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from typing import Sequence
from datetime import datetime
from ...schemas.wealth_portfolio_candidates import CurrentFacts,CandidateFilter
from ...schemas.wealth_portfolio_data import AssetModelFacts
from ...domain.wealth_portfolio_candidates import ProductIndex
from ...schemas.wealth_portfolio import (PortfolioPlan,PortfolioSelection,SelectionItem,AdvisedPosition,PortfolioModelView,CandidateGroup)
from ...schemas.wealth_portfolio_lifecycle import PortfolioCreate,PortfolioRecommendation,SelectedShare
from ...schemas.wealth_portfolio_candidates import ShareReference,SelectionRequest,NextBatchRequest,SessionAction
from ...domain.wealth_portfolio_allocation import problem,validate_adjustment,integer_valid,BUCKETS,ModelError
from ...domain.wealth_portfolio_projection import project_products
from ...domain.wealth_portfolio_eligibility import qualify_purchase,version_context,offer_is_current
from .wealth_portfolio_model import PortfolioModelProvider,ratio_strings,adapt_candidate_group,snapshot_content
from .wealth_portfolio_candidates import CandidateError
from .wealth_portfolio_store import PortfolioStore,PortfolioRecord
from .wealth_portfolio_access import PortfolioAccessSource,access_binding
from .wealth_portfolio_data import digest

class PortfolioLifecycle:
    def __init__(self,provider: PortfolioModelProvider,store: PortfolioStore,access: PortfolioAccessSource | None):
        self.provider=provider;self.store=store;self.access=access;self.candidate_contexts={}

    async def _access(self,user: str,context: str,write: bool,pid: str | None=None) -> dict:
        if self.access is None:raise CandidateError('PORTFOLIO_CONTEXT_SOURCE_UNAVAILABLE',503)
        grant=deepcopy(await self.access.resolve(user,context,write=write,portfolio_id=pid))
        required={'user_id','context_id','context_epoch','asset_scope_id','account_ids','can_read'}
        epoch = grant.get('context_epoch')
        switch = grant.get('selection_version')
        if (not required<=grant.keys() or epoch is not None and (type(epoch) is not int or epoch<1)
            or epoch is None and (type(switch) is not int or switch<1)
            or 'selection_version' in grant and (type(switch) is not int or switch<1)
            or not isinstance(grant.get('asset_scope_id'),str) or not grant['asset_scope_id']
            or grant.get('session_ref') is not None and (not isinstance(grant['session_ref'],str) or not grant['session_ref'])
            or type(grant.get('can_read')) is not bool or not isinstance(grant.get('account_ids'),list)
            or not grant['account_ids'] or any(not isinstance(a,str) or not a for a in grant['account_ids'])
            or len(set(grant['account_ids']))!=len(grant['account_ids'])):raise CandidateError('PORTFOLIO_CONTEXT_UNPROVEN',503)
        if grant['user_id']!=user or grant['context_id']!=context or not grant['can_read']:raise CandidateError('PORTFOLIO_CONTEXT_FORBIDDEN',403)
        if grant.get('session_closed') is True:raise CandidateError('PORTFOLIO_SESSION_CLOSED',409)
        if write and (grant.get('stop_new_submissions') is True or grant.get('control_mode') in ('HUMAN','HANDOFF_PENDING') or grant.get('status')=='PAUSED'):
            raise CandidateError('TASK_WRITE_BLOCKED',409)
        if write and type(grant.get('can_edit')) is not bool:raise CandidateError('PORTFOLIO_WRITE_CONTROL_UNPROVEN',503)
        if write and not grant['can_edit']:raise CandidateError('TASK_WRITE_BLOCKED',409)
        return grant

    async def _facts(self,user: str,grant: dict) -> tuple[CurrentFacts,ProductIndex,datetime,AssetModelFacts,str]:
        facts,index,now,asset=await self.provider._facts(user)
        snap=asset.snapshot
        if (snap.context_id,snap.context_epoch,snap.asset_scope_id,sorted(snap.account_ids))!=(grant['context_id'],grant['context_epoch'],grant['asset_scope_id'],sorted(grant['account_ids'])):
            raise CandidateError('PORTFOLIO_ASSET_SCOPE_STALE')
        stamp=digest({'snapshot':snapshot_content(snap),'qualification':version_context(facts).model_dump(mode='json'),
            'products_fingerprint':index.content_fingerprint,
            'offer_validity':{code:offer_is_current(o,facts,now) for code,o in facts.offers.items()}})
        return facts,index,now,asset,stamp

    @staticmethod
    def _refs(items: Sequence[SelectedShare | SelectionItem], facts: CurrentFacts, index: ProductIndex) -> list[ShareReference]:
        refs=[]
        for item in items:
            product=index.products.get(item.product_id)
            if not product:raise CandidateError('PRODUCT_NOT_FOUND',422)
            outward=product.share_class if product.share_class in ('A','C') else None
            if (item.product_version,item.share_class)!=(product.product_version,outward):raise CandidateError('SELECTED_SHARE_IDENTITY_MISMATCH',422)
            offer=facts.offers.get(product.product_id)
            if offer is None:raise CandidateError('CURRENT_BANK_PRODUCT_UNAVAILABLE')
            if product.disclosure_version is None or offer.rules_version is None:raise CandidateError('CURRENT_RULES_INCOMPLETE',422)
            refs.append(ShareReference(product_id=product.product_id,product_version=product.product_version,disclosure_version=product.disclosure_version,
                rules_version=offer.rules_version,share_class=product.share_class,target_ratio_total='0'))
        return refs

    def _qualify(self,user: str,refs: list[ShareReference],facts: CurrentFacts,index: ProductIndex,now: datetime) -> None:
        if not refs:return
        result=self.provider.candidates.validate_current(SelectionRequest(expected_context=version_context(facts),items=refs),facts,index,user,now)
        if not result.accepted:raise CandidateError('PORTFOLIO_SELECTION_REJECTED',422,{'reason_codes':result.reason_codes,'failures':result.failures})

    def _check_candidate_binding(self,user: str,request: PortfolioCreate,grant: dict,facts: CurrentFacts) -> None:
        binding=request.candidate_binding
        if binding is None:return
        candidates=self.provider.candidates;owned=candidates.sessions.get(binding.session_id)
        if owned is None or owned.user_id!=user:raise CandidateError('CANDIDATE_SESSION_NOT_FOUND',404)
        state=candidates._state(user,SessionAction(session_id=binding.session_id,expected_version=binding.expected_version))
        expected=access_binding(grant)
        original=deepcopy(self.candidate_contexts.get(binding.session_id))
        if original is not None and original.get('session_ref') is None:
            expected.pop('session_ref',None);original.pop('session_ref',None)
        if original!=expected or state.filter!=binding.filter or state.context!=version_context(facts) or state.requires_refresh:
            raise CandidateError('PORTFOLIO_CANDIDATE_BINDING_STALE')
        wanted={(r.product_id,r.product_version,r.share_class) for r in request.selected or []}
        present={(r['reference']['product_id'],r['reference']['product_version'],r['reference']['share_class'] if r['reference']['share_class'] in ('A','C') else None) for r in state.selected.values()}
        if wanted!=present:raise CandidateError('PORTFOLIO_CANDIDATE_SELECTION_STALE')

    async def _candidate_binding(self,user: str,request: PortfolioCreate,grant: dict,facts: CurrentFacts) -> None:
        async with self.provider.candidates.lock:self._check_candidate_binding(user,request,grant,facts)

    def _decorate(self,plan: PortfolioPlan,meta: dict,explicit: list[ShareReference],facts: CurrentFacts,index: ProductIndex,now: datetime) -> PortfolioPlan:
        plan=plan.model_copy(deep=True);snap=meta['snapshot'];current={};accounts={}
        for pos in snap['positions']:
            code=pos['product_id'];current[code]=current.get(code,0)+(pos['market_value_fen'] or 0);accounts.setdefault(code,set()).add(pos['account_id'])
        rows=[];chosen={r.product_id for r in explicit};decisions={}
        for bucket,projection in meta['product_projections'].items():
            for family in projection['targets']:
                for part in family['parts']:
                    p=index.products[part['product_id']];q=qualify_purchase(p,facts,facts.user_id,now)
                    decisions[p.product_id]=q
                    role='holding' if p.product_id in current else 'selected' if p.product_id in chosen else 'advice'
                    rows.append(AdvisedPosition(product_id=p.product_id,product_version=p.product_version,disclosure_version=p.disclosure_version,share_class=p.share_class if p.share_class in ('A','C') else None,
                        original_share_class=p.share_class,family_id=family['family_id'],name=p.name,bucket=bucket,target_fen=part['target_fen'],current_fen=current.get(p.product_id,0),
                        role=role,account_ids=sorted(accounts.get(p.product_id,[])),holding_cap_fen=current.get(p.product_id,0) if not q.purchase_eligible else None))
        meta['accepted_targets']={'bucket_amounts_fen':[b.target_fen for b in plan.buckets],
            'products_fen':{r.product_id:r.target_fen for r in rows},'cash_residual_fen':plan.cash_residual_fen,
            'actual_growth_ratio_ex_cash':plan.growth_ratio_ex_cash}
        for row in meta.get('positions',[]):
            row['target_fen']=meta['accepted_targets']['products_fen'].get(row['position']['product_id'],0)
        model=meta['model'];plan.advised_positions=rows
        # A recommendation batch is not the complete selection. Persist the
        # qualified exact-code cards for cross-batch selection and human review.
        # These are existing facts, not new DPP recommendations or trade consent.
        from ...domain.wealth_portfolio_candidates import family_candidates
        selected_products=[index.products[r.product_id] for r in explicit if r.product_id in decisions and decisions[r.product_id].purchase_eligible]
        if selected_products:
            families,_,_=family_candidates(index,selected_products,[decisions[p.product_id] for p in selected_products],facts,now)
            selected=adapt_candidate_group({'status':'ok','source_kind':facts.mode,
                'fixture_only':facts.mode=='fixture','items':[f.model_dump(mode='json') for f in families],
                'pipeline':{'reason_codes':[],'exclusions':{},'risk_explanation':None}},
                index,CandidateFilter(mode='general'))
            # With explicit selection, Plan cards describe that selection;
            # unrelated recommendation-batch cards remain in the A05 session.
            for group in plan.candidate_groups:group.items=[]
            for card in selected.items:
                group=next(g for g in plan.candidate_groups if g.bucket==card.bucket)
                card.reason='本人选中的具体代码；当前资格与投资资料已核实，尚未授权交易。'
                card.reason_codes=['QUALIFIED_SHARE','USER_SELECTED_EXACT_CODE']
                group.items=[c for c in group.items if c.family_id!=card.family_id]+[card]
                group.status='ok'
        plan.model_view=PortfolioModelView(initial_ratios=[str(x) for x in model['continuous_solution']],slider_bounds=None if model['slider'] is None else [str(v) for v in model['slider']],
            available_cash_fen=meta['available_cash_fen'],receivable_fen=meta['receivable_not_payable_fen'],held_fen=sum(a['held_fen'] for a in snap['accounts']),snapshot=snap)
        return PortfolioPlan.model_validate(plan.model_dump())

    async def create(self,user_id: str,request: PortfolioCreate) -> PortfolioPlan:
        grant=await self._access(user_id,request.context_id,True)
        facts,index,now,asset,stamp=await self._facts(user_id,grant)
        await self._candidate_binding(user_id,request,grant,facts)
        refs=None if request.selected is None else self._refs(request.selected,facts,index)
        if refs is not None:self._qualify(user_id,refs,facts,index,now)
        result=await self.provider.plan(user_id,request.growth_ratio_ex_cash,refs,
            _initial_facts=(facts,index,now,asset),_defer_final_validation=True)
        if result.status!='ok' or result.plan is None:raise CandidateError((result.reason_codes or result.missing_fields or ['PORTFOLIO_'+result.status.upper()])[0],422)
        plan=self._decorate(result.plan,result.metadata,refs or [],facts,index,now)
        plan.portfolio_id='portfolio-'+secrets.token_urlsafe(24);plan.version=1
        record=PortfolioRecord(user_id,grant,stamp,plan,deepcopy(result.metadata),refs or [])
        await self._fresh(record,request.context_id,True,creating=True)
        if request.candidate_binding:
            async with self.provider.candidates.lock:
                self._check_candidate_binding(user_id,request,grant,facts)
                record.metadata['candidate_binding']=request.candidate_binding.model_dump(mode='json')
                await self.store.insert(record)
        else:await self.store.insert(record)
        return plan.model_copy(deep=True)

    async def _fresh(self,record: PortfolioRecord,context: str,write: bool,*,creating: bool=False) -> tuple[CurrentFacts,ProductIndex,datetime,AssetModelFacts]:
        if context!=record.plan.context_id:raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        # A new plan has not entered the store yet. Recheck the actual current
        # Action session; existing plans always resolve their recorded session.
        grant=await self._access(record.user_id,context,write,None if creating else record.plan.portfolio_id)
        if access_binding(grant)!=access_binding(record.access):raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        try:facts,index,now,asset,stamp=await self._facts(record.user_id,grant)
        except ModelError as error:raise CandidateError(error.code,409,error.details) from error
        if stamp!=record.fingerprint:raise CandidateError('PORTFOLIO_FACTS_STALE')
        # 到期不一定改变内容版本，仍逐原具体份额验证有效期限。
        refs=[ShareReference.model_validate(r) for r in record.metadata['validation_contract']['selected_references']]
        try:self._qualify(record.user_id,refs,facts,index,now)
        except CandidateError as error:raise CandidateError('PORTFOLIO_SHARE_STALE',409,error.details) from error
        for row in record.plan.advised_positions:
            if row.account_ids and qualify_purchase(index.products[row.product_id],facts,record.user_id,now).holding_allowed is not True:
                raise CandidateError('PORTFOLIO_HOLDING_PERMISSION_STALE')
        return facts,index,now,asset

    async def get(self,user_id: str,portfolio_id: str,context_id: str) -> PortfolioPlan:
        record=await self.store.read(portfolio_id,user_id)
        await self._fresh(record,context_id,False)
        return record.plan.model_copy(deep=True)

    async def get_for_execution(self,user_id: str,portfolio_id: str,context_id: str) -> PortfolioPlan:
        """Selected-code freshness for execution, never a cached permission.

        A full-directory fingerprint belongs to recommendation. Execution binds
        the same live asset/assessment/access facts and freshly reads every
        selected or advised code, including zero targets and existing holdings.
        """
        record=await self.store.read(portfolio_id,user_id)
        if context_id!=record.plan.context_id:raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        grant=await self._access(user_id,context_id,True,portfolio_id)
        if access_binding(grant)!=access_binding(record.access):raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        facts,index,now,asset=await self.provider._facts(user_id)
        old=deepcopy(record.metadata['snapshot']);current=snapshot_content(asset.snapshot)
        old.pop('as_of',None)
        from zoneinfo import ZoneInfo
        old['business_at']=datetime.fromisoformat(old['business_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        # Scoped offers have a different catalogue digest. All selected rules
        # are checked below; data/daily and all dynamic snapshot facts remain.
        old['data_versions'].pop('offers_version',None)
        current['data_versions'].pop('offers_version',None)
        if current!=old:raise CandidateError('PORTFOLIO_FACTS_STALE')
        original=record.metadata['validation_contract']['qualification_context']
        original=original.model_dump(mode='json') if hasattr(original,'model_dump') else original
        present=version_context(facts).model_dump(mode='json')
        for key in ('user_id','source_id','mode','channel','data_version','daily_version','assessment_id','assessment_version'):
            if present.get(key)!=original.get(key):raise CandidateError('PORTFOLIO_FACTS_STALE')
        refs=[ShareReference.model_validate(r) for r in record.metadata['validation_contract']['selected_references']]
        self._qualify(user_id,refs,facts,index,now)
        for row in record.plan.advised_positions:
            if row.account_ids and qualify_purchase(index.products[row.product_id],facts,user_id,now).holding_allowed is not True:
                raise CandidateError('PORTFOLIO_HOLDING_PERMISSION_STALE')
        return record.plan.model_copy(deep=True)

    @staticmethod
    def _fen(text: str,total: int,original_fen: int | None=None) -> int:
        if original_fen is not None and Decimal(text)==Decimal(original_fen)/Decimal(total):return original_fen
        value=Fraction(text)*total
        if value.denominator!=1:raise CandidateError('PORTFOLIO_AMOUNT_PRECISION',422)
        return value.numerator

    def _manual(self,record: PortfolioRecord,request: PortfolioSelection,facts: CurrentFacts,index: ProductIndex,now: datetime,asset: AssetModelFacts) -> tuple[PortfolioPlan,dict,list[ShareReference]]:
        old=record.plan;meta=record.metadata;T=old.basis_fen;contract=meta['validation_contract'];calc=contract['calculation']
        P=problem(facts.assessment.capacity_level,calc['h'],contract['available'],contract['caps'],contract['p'])
        same_items=[i.model_dump() for i in request.items]==[i.model_dump() for i in old.selected_positions]
        slider=request.growth_ratio_ex_cash is not None and same_items and request.cash_residual_fen is None
        if slider:
            c=old.buckets[0].target_fen;noncash=T-c
            if not noncash or facts.assessment.capacity_level==1:raise ModelError('PREFERENCE_NOT_APPLICABLE_R1' if facts.assessment.capacity_level==1 else 'PREFERENCE_NONCASH_UNAVAILABLE')
            g=self._fen(request.growth_ratio_ex_cash,noncash);amounts=[c,noncash-g,g]
            refs=[ShareReference.model_validate(r) for r in contract['selected_references']]
            self._qualify(record.user_id,refs,facts,index,now)
            families,_,_,missing=self.provider._families(asset,facts,index,now,refs)
            if missing:raise CandidateError('HOLDING_IDENTITY_INCOMPLETE',422)
            outputs={b:deepcopy(meta['product_projections'][b]) if amounts[i]==old.buckets[i].target_fen else project_products(sorted(families[b].values(),key=lambda f:f.family_id),amounts[i],T) for i,b in enumerate(BUCKETS)}
            targets={p['product_id']:p['target_fen'] for output in outputs.values() for f in output['targets'] for p in f['parts']}
            cash=next(f['target_fen'] for f in outputs['cash_management']['targets'] if f['cash'])
            explicit=deepcopy(record.explicit)
        else:
            held={r.product_id:r for r in old.advised_positions if r.account_ids}
            targets={code:r.target_fen for code,r in held.items()};explicit=[]
            for item in request.items:
                ref=self._refs([item],facts,index)[0];original_row=next((r for r in old.advised_positions if r.product_id==item.product_id),None);value=self._fen(item.target_ratio_total,T,original_row.target_fen if original_row else None)
                q=qualify_purchase(index.products[item.product_id],facts,record.user_id,now,ref)
                original=held.get(item.product_id)
                if original and not q.purchase_eligible:
                    if q.holding_allowed is not True or value>original.current_fen:raise CandidateError('PORTFOLIO_HOLDING_CAP_EXCEEDED',422)
                else:
                    self._qualify(record.user_id,[ref],facts,index,now);explicit.append(ref)
                targets[item.product_id]=value
            self._qualify(record.user_id,explicit,facts,index,now)
            cash=T-sum(targets.values())
            if cash<0:raise CandidateError('PORTFOLIO_OVERALLOCATED',422)
            if request.cash_residual_fen is not None and request.cash_residual_fen!=cash:raise CandidateError('PORTFOLIO_CASH_MISMATCH',422)
            amounts=[cash,0,0];families={}
            for code,value in targets.items():
                p=index.products[code];amounts[BUCKETS.index(p.bucket)]+=value
                family=next((r.family_id for r in old.advised_positions if r.product_id==code),None)
                if family is None:
                    from ...domain.wealth_portfolio_candidates import reliable_family
                    family,errors=reliable_family(p,now)
                    if errors:raise CandidateError('FAMILY_UNVERIFIED',422)
                families.setdefault(p.bucket,{}).setdefault(family,[]).append({'product_id':code,'target_fen':value})
            outputs={b:{'targets':[{'family_id':f,'cash':False,'target_fen':sum(r['target_fen'] for r in rows),'parts':rows} for f,rows in families.get(b,{}).items()]} for b in BUCKETS}
            outputs['cash_management']['targets'].append({'family_id':'__cash_asset__','cash':True,'target_fen':cash,'parts':[]})
        check=validate_adjustment(P,calc['continuous_solution'],[a/T for a in amounts])
        if not check['accepted']:raise CandidateError(check['reason_codes'][0],422,check)
        if not integer_valid(P,amounts,T,request=False):raise CandidateError('MANUAL_INTEGER_POLICY_INVALID',422)
        if not slider:
            manual_contract=deepcopy(contract);manual_contract['selected_references']=[r.model_dump(mode='json') for r in explicit]
            family_targets={r['family_id']:r['target_fen'] for output in outputs.values() for r in output['targets']}
            decision=self.provider.validate_weights(record.user_id,facts,index,now,asset,manual_contract,family_targets,amounts)
            if not decision['accepted']:raise CandidateError(decision['reason_codes'][0],422,decision)
        actual_p=None if amounts[0]==T else str(Decimal(amounts[2])/Decimal(T-amounts[0]))
        if request.growth_ratio_ex_cash is not None and not slider and (actual_p is None or abs(Decimal(actual_p)-Decimal(request.growth_ratio_ex_cash))>Decimal('0.000001')):
            raise CandidateError('PORTFOLIO_GROWTH_RATIO_MISMATCH',422)
        changed=deepcopy(meta);changed['product_projections']=outputs
        # 原U不变；只有选择引用变化，原优化/舍入台账仍保留。
        if not slider:changed['validation_contract']['selected_references']=[r.model_dump(mode='json') for r in explicit]
        plan=old.model_copy(deep=True);ratios=ratio_strings(amounts,T)
        for i,b in enumerate(plan.buckets):b.target_fen=amounts[i];b.target_ratio=ratios[i]
        plan.cash_residual_fen=cash;plan.growth_ratio_ex_cash=actual_p
        chosen={r.product_id for r in explicit}
        plan.selected_positions=[SelectionItem(product_id=code,product_version=index.products[code].product_version,share_class=index.products[code].share_class if index.products[code].share_class in ('A','C') else None,target_ratio_total=str(Decimal(value)/T)) for code,value in targets.items() if code in chosen]
        plan=self._decorate(plan,changed,explicit,facts,index,now)
        # 后续滑块固定当前活钱，界限仍围绕原x*/U，仅重新取该固定c截面。
        star=calc['continuous_solution'];fixed=amounts[0]/T
        if amounts[0]!=T:
            # 凸有效集的固定现金截面，两端二分并逐点用原validator检查。
            lo,hi=0.,1.
            def valid(p: float) -> bool:return validate_adjustment(P,star,[fixed,(1-fixed)*(1-p),(1-fixed)*p])['accepted']
            center=amounts[2]/(T-amounts[0])
            for side in (0,1):
                a,b=(0.,center) if side==0 else (center,1.)
                for _ in range(55):
                    mid=(a+b)/2
                    if valid(mid):
                        if side==0:b=mid
                        else:a=mid
                    elif side==0:a=mid
                    else:b=mid
                if side==0:lo=b
                else:hi=a
            plan.model_view.slider_bounds=[str(lo),str(hi)]
        else:plan.model_view.slider_bounds=None
        return plan,changed,explicit

    async def revise(self,user_id: str,portfolio_id: str,request: PortfolioSelection) -> PortfolioPlan:
        if portfolio_id!=request.portfolio_id:raise CandidateError('PORTFOLIO_PATH_BODY_MISMATCH',422)
        record=await self.store.read(portfolio_id,user_id)
        if request.expected_version!=record.plan.version:raise CandidateError('PORTFOLIO_VERSION_STALE')
        if request.snapshot_id!=record.plan.snapshot_id:raise CandidateError('PORTFOLIO_SNAPSHOT_STALE')
        facts,index,now,asset=await self._fresh(record,request.context_id,True)
        if request.revision_kind=='preference':
            if request.growth_ratio_ex_cash is None:raise CandidateError('PORTFOLIO_PREFERENCE_REQUIRED',422)
            refs=self._refs(request.items,facts,index) if request.items else deepcopy(record.explicit)
            previous={(r.product_id,r.product_version,r.share_class) for r in record.explicit}
            if not previous<={(r.product_id,r.product_version,r.share_class) for r in refs}:raise CandidateError('PORTFOLIO_PREFERENCE_SELECTION_MISMATCH',422)
            self._qualify(user_id,refs,facts,index,now)
            # 未显式改集合时自动建议保持自动身份，不转为已选。
            selected=refs if refs else None
            result=await self.provider.plan(user_id,request.growth_ratio_ex_cash,selected)
            if result.plan is None or result.status!='ok':raise CandidateError('PORTFOLIO_PREFERENCE_INFEASIBLE',422)
            plan=self._decorate(result.plan,result.metadata,refs,facts,index,now);meta=result.metadata;explicit=refs
        else:plan,meta,explicit=self._manual(record,request,facts,index,now,asset)
        plan.portfolio_id=portfolio_id;plan.version=record.plan.version+1
        replacement=PortfolioRecord(user_id,deepcopy(record.access),record.fingerprint,plan,deepcopy(meta),explicit)
        await self._fresh(record,request.context_id,True)
        await self.store.replace(replacement,request.expected_version)
        return plan.model_copy(deep=True)

    async def recommend(self,user_id: str,request: PortfolioRecommendation) -> CandidateGroup:
        if request.filter.mode=='theme':raise CandidateError('THEME_MODE_REMOVED',410)
        grant=await self._access(user_id,request.context_id,False)
        candidates=self.provider.candidates
        facts,index,now=await candidates.current(user_id)
        refs=self._refs(request.selected,facts,index);self._qualify(user_id,refs,facts,index,now)
        if request.cursor:
            binding=request.candidate_binding
            async with candidates.lock:
                state=candidates._state(user_id,SessionAction(session_id=binding.session_id,expected_version=binding.expected_version))
                if self.candidate_contexts.get(binding.session_id)!=access_binding(grant) or state.filter!=request.filter or binding.filter!=request.filter:raise CandidateError('PORTFOLIO_CANDIDATE_BINDING_STALE')
            raw=await candidates.next_batch(user_id,NextBatchRequest(session_id=binding.session_id,expected_version=binding.expected_version,cursor=request.cursor),_current=(facts,index,now))
            raw['source_kind']=facts.mode;raw['fixture_only']=facts.mode=='fixture'
        else:
            raw=await candidates.candidates(user_id,request.filter,_current=(facts,index,now))
            if raw.get('session'):
                sid=raw['session']['session_id'];self.candidate_contexts[sid]=access_binding(grant)
                if refs:
                    from ...domain.wealth_portfolio_candidates import reliable_family,pipeline
                    async with candidates.lock:
                        state=candidates.sessions[sid];state.displayed=set();state.rotation_position=0
                        state.selected={r.product_id:{'reference':r.model_dump(mode='json'),'family_id':reliable_family(index.products[r.product_id],now)[0],'valid':True,'reason_codes':[]} for r in refs}
                        result=pipeline(index,request.filter,facts,user_id,now)
                        raw.update(candidates._batch(state,result,facts));raw['session']=candidates._view(state)
        group=adapt_candidate_group(raw,index,request.filter)
        if access_binding(await self._access(user_id,request.context_id,False))!=access_binding(grant):
            raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        # 同协议可读会话引用，不替换CandidateGroup或创建额外结果DTO。
        group.session_binding=raw.get('session')
        return group
