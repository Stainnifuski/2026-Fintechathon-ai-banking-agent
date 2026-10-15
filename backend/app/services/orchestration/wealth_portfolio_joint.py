"""Canonical A provider over B's authenticated bank facts; no NLP or trading."""
import os
from pathlib import Path
from datetime import datetime,timezone
from copy import deepcopy
from sqlalchemy import select
from ...integrations.wealth_portfolio_bank_facts import BankPortfolioFacts,business_content
from ...integrations.wealth_authorization import latest_assessment,assessment_view,aware
from ...models.wealth import WealthProfileORM,WealthTaskORM,WealthConversationORM
from .wealth_portfolio import build_snapshot,SnapshotError
from .wealth_portfolio_candidates import CandidateService,SnapshotIndexCache,CandidateError
from .wealth_portfolio_data import read,digest
from .wealth_portfolio_model import PortfolioModelProvider
from .wealth_portfolio_lifecycle import PortfolioLifecycle
from .wealth_portfolio_store import MemoryPortfolioStore
from ...schemas.wealth_portfolio_lifecycle import PortfolioCreate,PortfolioRecommendation
from ...schemas.wealth_portfolio_candidates import CandidateFilter
from ...schemas.wealth_portfolio import PortfolioSelection
from ...domain.wealth_portfolio_allocation import ModelError
from ...integrations.wealth_authorization import WealthPolicyError

async def invoke(call):
    try:return await call
    except (CandidateError,SnapshotError,ModelError) as error:
        details=getattr(error,'details',None)
        reasons=details.get('reason_codes',[]) if isinstance(details,dict) else []
        message=str(error)+('：'+', '.join(r for r in reasons if isinstance(r,str)) if reasons else '')
        raise WealthPolicyError(message,error.code,getattr(error,'status_code',422)) from error

DATA='bfa4f29d6bfae78f7fe228c24fe3fcdda641107ffa23efadd851af94e831d33b'
DAILY='770e273fa9e1a0bfbd8c34286628224ab90788a60a06729d93239efd20922e6f'

class ServerReads:
    owns_responses=True # protected HTTP returns request-owned new JSON, never a shared bank cache
    frozen_catalogues={}
    catalogue_reads=0
    def __init__(self,db,user,bank,index=None,product_ids=None):
        self.db,self.user,self.bank,self.index=db,user,bank,index
        self.product_ids=product_ids
        self.selected_response=None
    async def get(self,path):
        if path=='/api/v1/auth/me':return {'user_id':self.user}
        suffix=path.removeprefix('/api/v1/wealth/')
        if suffix=='assets':return await self.bank.assets(self.user)
        if suffix=='products':
            if self.selected_response is not None and self.product_ids is not None:
                return self.selected_response
            # Competition rules are immutable; scope is proven anew on every
            # request. Cache no assessment, balance, authorization or control.
            contexts=await self.bank.call('POST','/contexts',{'subject_user_id':self.user})
            scope=next((c for c in contexts['items'] if c['context_id']==contexts['selected']),{})
            key=(id(self.db.get_bind()),os.getenv('BANK_API_BASE_URL'),contexts['selected'] if not scope.get('dataset_key') else scope['dataset_key'],
                tuple(sorted(scope.get('additional_dataset_keys',[]))))
            cached=self.frozen_catalogues.get(key) if self.product_ids is None and scope.get('dataset_key','').startswith('real-public-paper-v1:') else None
            if cached is not None:
                from datetime import date
                from zoneinfo import ZoneInfo
                current=await self.bank.assets(self.user)
                day=datetime.fromisoformat(current['business_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
                return [{**row,'purchase_open':(day-date(2026,9,30)).days%row['rules'].get('open_interval_days',1)==0} for row in cached]
            if hasattr(self.bank,'call'):
                references=getattr(self.index,'policy_references',{})
                body={'subject_user_id':self.user,'facts_only':True,'policy_references':bool(references)}
                if self.product_ids is not None:body['product_ids']=self.product_ids
                from ...integrations.bank_api import BankApiError
                try:rows=await self.bank.call('POST','/catalog',body)
                except BankApiError as error:raise CandidateError(error.code,error.status_code) from error
                for row in rows:
                    if 'execution_policy_sha256' not in row:continue
                    code=row['product_id'];binding=references.get(code)
                    actual=row.pop('execution_policy_sha256')
                    row['rules']['execution_policy']=binding[1] if binding and binding[0]==actual else None
                if self.product_ids is None and scope.get('dataset_key','').startswith('real-public-paper-v1:'):
                    self.frozen_catalogues[key]=rows
                    type(self).catalogue_reads+=1
                return rows
            return await self.bank.products(self.user)
        if suffix=='contexts':return await self.bank.call('POST','/contexts',{'subject_user_id':self.user})
        if suffix=='assessments/current':
            row=await latest_assessment(self.db,self.user)
            return assessment_view(row) if row else None
        raise CandidateError('BANK_READ_PATH_DENIED',403)

class JointFacts(BankPortfolioFacts):
    """Use B's full summary and explicit lot versions, never the legacy run adapter."""
    completed_read_time=True
    frozen_offers={}
    async def quote_cards(self,user_id,group,index):
        codes=sorted({s.product_id for card in group.items for s in card.share_classes})
        if codes and hasattr(self.transport.bank,'selected_products'):
            rows=await self.transport.bank.selected_products(user_id,codes)
            old_ids=self.transport.product_ids
            self.transport.product_ids=codes;self.transport.selected_response=rows
            try:fresh=await self.read_current(user_id,datetime.now(timezone.utc))
            finally:self.transport.product_ids=old_ids;self.transport.selected_response=None
            from ...domain.wealth_portfolio_eligibility import qualify_purchase
            prices={r['product_id']:r for r in rows if r.get('nav') is not None and r['product_id'] in index.products and
                qualify_purchase(index.products.get(r['product_id']),fresh,user_id,fresh.read_at).purchase_eligible}
            group.items=[card for card in group.items if all(s.product_id in prices for s in card.share_classes)]
            for card in group.items:
                card.evidence['current_quotes']={s.product_id:{k:prices[s.product_id].get(k) for k in ('nav','nav_date','price_source','available_at')} for s in card.share_classes}
            if not group.items:group.status='unavailable';group.reason_codes=['CURRENT_DISPLAY_QUALIFICATION_UNAVAILABLE']
        return group
    async def complete_current_round(self,user_id,facts):
        """Recheck dynamic facts without rereading frozen material in this round.

        A later lifecycle/prepare call creates a new protected product read.
        No account, assessment, control or permission survives this request.
        """
        assets=await self.transport.bank.assets(user_id)
        assessment=await self.transport.get('/api/v1/wealth/assessments/current')
        contexts=await self.transport.get('/api/v1/wealth/contexts')
        values={'assets':assets,'assessment':assessment,'contexts':contexts}
        if any(business_content(value)!=business_content(self.observations[key]) for key,value in values.items()):return None
        self.observations.update(values)
        completed=datetime.now(timezone.utc);facts.read_at=completed
        if facts.assessment:facts.assessment.read_at=completed
        for offer in facts.offers.values():
            if offer.current_simulation:offer.current_simulation.observed_at=completed
        return facts
    async def read(self,user_id):
        if user_id!=self.transport.user:raise SnapshotError('subject mismatch','FACT_SCOPE_FORBIDDEN',403)
        raw=await self.transport.bank.assets(user_id)
        if 'asset_summary' not in raw:raise SnapshotError('shared summary unavailable','ASSET_SUMMARY_UNAVAILABLE',503)
        assessment=await self.transport.get('/api/v1/wealth/assessments/current')
        contexts=await self.transport.get('/api/v1/wealth/contexts')
        # Explicit lot versions already prove identity. Only legacy lots that
        # lack a version need protected product resolution; do not scan the
        # full directory merely to value an empty or small holding set.
        unresolved=sorted({l['product_id'] for l in raw['lots'] if l.get('product_version') is None})
        if hasattr(self.transport.bank,'selected_products'):
            products=await self.transport.bank.selected_products(user_id,unresolved)
        else:products=await self.transport.bank.products(user_id)
        return self._bundle(raw,assessment,contexts,products)
    async def read_from_current(self,user_id):
        # Used only by _facts between fresh current reads. This bundle is
        # request-local; final lifecycle validation rereads all current facts.
        if user_id!=self.transport.user:raise SnapshotError('subject mismatch','FACT_SCOPE_FORBIDDEN',403)
        if not self.observations.get('assets'):return await self.read(user_id)
        return self._bundle(self.observations['assets'],self.observations['assessment'],
                            self.observations['contexts'],self.observations['products'])
    def _bundle(self,raw,assessment,contexts,products):
        if contexts['selected']!=raw['context_id']:raise SnapshotError('context changed','PORTFOLIO_CONTEXT_STALE',409)
        current=next((c for c in contexts['items'] if c['context_id']==raw['context_id']),{})
        fields=('account_id','account_type','balance_fen','held_fen','available_fen')
        ids=set(raw['asset_summary']['account_ids'])
        return {'summary':deepcopy(raw['asset_summary']),
            'accounts':[{k:a[k] for k in fields} for a in raw['cash'] if a['account_id'] in ids],
            'lots':[deepcopy(l) for l in raw['lots'] if l['account_id'] in ids],
            'orders':[deepcopy(o) for o in raw['orders'] if o['account_id'] in ids],
            'products':products,'basis_status':'confirmed',
            'context_id':raw['context_id'],'context_epoch':current.get('context_epoch'),
            'assessment':{k:assessment[k] for k in ('assessment_id','capacity_level','purchase_allowed','expires_at','confirmed')} if assessment else None,
            'assessment_provenance':{'source':'protected-local-bank','version':'adapter:'+digest(assessment) if assessment else None},
            'data_versions':{'data_version':self.data_version,'daily_version':self.daily_version,'offers_version':self.current_products_version if products is self.observations.get('products') else 'adapter:'+digest(products)}}
    async def read_current(self,user_id,at):
        # Only immutable catalogue templates are reused. Every observation of
        # identity, assessment, scope, business time and assets remains fresh.
        from ...schemas.wealth_portfolio_candidates import FormalAssessment,CurrentFacts,VersionContext
        contexts=await self.transport.get('/api/v1/wealth/contexts')
        scope=next((c for c in contexts['items'] if c['context_id']==contexts['selected']),{})
        key=(id(self.transport.db.get_bind()),os.getenv('BANK_API_BASE_URL'),scope.get('dataset_key'),tuple(sorted(scope.get('additional_dataset_keys',[]))))
        template=self.frozen_offers.get(key) if self.transport.product_ids is None and scope.get('dataset_key','').startswith('real-public-paper-v1:') else None
        if template is None:
            facts=await super().read_current(user_id,at)
            if self.transport.product_ids is None and scope.get('dataset_key','').startswith('real-public-paper-v1:'):
                self.frozen_offers[key]=facts
        else:
            raw=await self.transport.get('/api/v1/wealth/assessments/current')
            assets=await self.transport.bank.assets(user_id)
            products=await self.transport.get('/api/v1/wealth/products')
            self.observations={'assessment':raw,'assets':assets,'contexts':contexts,'products':products}
            self.current_products_version=template.offers_version
            assessment=FormalAssessment(user_id=user_id,assessment_id=raw['assessment_id'],version='adapter:'+digest(raw),
                source_id='protected-local-bank',source_kind='simulated',capacity_level=raw['capacity_level'],
                confirmed=raw['confirmed'],purchase_allowed=raw['purchase_allowed'],expires_at=raw['expires_at'],read_at=at) if raw else None
            business=datetime.fromisoformat(assets['business_at'])
            offers={}
            for code,offer in template.offers.items():
                evidence=offer.current_simulation
                if evidence:
                    from ...domain.wealth import TZ,advance_business
                    from ...domain.wealth_paper_policy import PRICING_MODES
                    day=business.astimezone(TZ).date()
                    from datetime import date
                    opened=(day-date(2026,9,30)).days%evidence.open_interval_days==0
                    stale=evidence.pricing_mode not in PRICING_MODES and evidence.nav_date is not None and advance_business(evidence.nav_date,3,evidence.calendar_version)<day
                    evidence=evidence.model_copy(update={'business_at':business,'observed_at':at,'purchase_open':opened,'data_stale':stale})
                allowed=not(evidence.status!='on_sale' or evidence.purchase_paused or not evidence.purchase_open or evidence.data_stale) if evidence else offer.purchase_allowed
                offers[code]=offer.model_copy(update={'current_simulation':evidence,'purchase_allowed':allowed})
            facts=CurrentFacts.model_construct(user_id=user_id,source_id=template.source_id,mode=template.mode,channel=template.channel,
                read_at=at,data_version=template.data_version,daily_version=template.daily_version,offers_version=template.offers_version,
                assessment=assessment,latest_assessment_id=assessment.assessment_id if assessment else None,
                latest_assessment_version=assessment.version if assessment else None,assessment_current_proven=False,offers=offers)
        profile=await self.transport.db.get(WealthProfileORM,user_id)
        row=await latest_assessment(self.transport.db,user_id)
        facts.assessment_current_proven=bool(profile and row and row.profile_version==profile.version)
        completed=datetime.now(timezone.utc)
        facts.read_at=completed
        if facts.assessment:facts.assessment.read_at=completed
        for offer in facts.offers.values():
            if offer.current_simulation:offer.current_simulation.observed_at=completed
        from ...domain.wealth_portfolio_eligibility import version_context
        # This sealed object belongs to this completed read only. The next
        # protected read makes a new object and recomputes every binding.
        if self.transport.product_ids is None and scope.get('dataset_key','').startswith('real-public-paper-v1:'):
            business=self.observations['assets']['business_at'][:10]
            binding=VersionContext(user_id=user_id,source_id=facts.source_id,mode=facts.mode,channel=facts.channel,
                data_version=facts.data_version,daily_version=facts.daily_version,
                assessment_id=facts.assessment.assessment_id if facts.assessment else None,
                assessment_version=facts.assessment.version if facts.assessment else None,offers_version=facts.offers_version,
                facts_fingerprint=digest({'frozen_offers':facts.offers_version,'business_day':business,
                    'assessment':facts.assessment.version if facts.assessment else None,'user_id':user_id}))
        else:binding=version_context(facts)
        object.__setattr__(facts,'_verified_context',binding)
        return facts

class JointAccess:
    def __init__(self,facts,store=None):self.facts,self.store=facts,store
    async def resolve(self,user_id,context_id,*,write,portfolio_id=None):
        raw=await self.facts.transport.bank.assets(user_id)
        if raw['context_id']!=context_id:raise CandidateError('PORTFOLIO_CONTEXT_FORBIDDEN',403)
        contexts=await self.facts.transport.get('/api/v1/wealth/contexts')
        if contexts['selected']!=context_id:raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        ctx=next((c for c in contexts['items'] if c['context_id']==context_id),{})
        summary=raw['asset_summary']
        grant={'user_id':user_id,'context_id':context_id,'context_epoch':ctx.get('context_epoch'),
            'selection_version':contexts['version'],'asset_scope_id':summary['asset_scope_id'],
            'account_ids':summary['account_ids'],'can_read':True}
        if ctx.get('session_closed') is True:raise CandidateError('PORTFOLIO_SESSION_CLOSED',409)
        session_ref=None
        if portfolio_id and self.store:
            session_ref=(await self.store.read(portfolio_id,user_id)).access.get('session_ref')
        elif write:
            session_ref=self.facts.transport.db.info.get('wealth_portfolio_action_session')
        if session_ref:
            row=await self.facts.transport.db.get(WealthConversationORM,session_ref,populate_existing=True)
            if row is None or row.user_id!=user_id or row.data.get('context_id','legacy-v1')!=context_id:
                raise CandidateError('PORTFOLIO_SESSION_UNPROVEN',409)
            if row.data.get('closed') is True:raise CandidateError('PORTFOLIO_SESSION_CLOSED',409)
            grant['session_ref']=session_ref
        if write:
            if ctx.get('run_state')=='ARCHIVED':raise CandidateError('TASK_WRITE_BLOCKED',409)
            # Query the real control state; do not select an arbitrary historical session.
            from .wealth_task import TERMINAL
            tasks=list(await self.facts.transport.db.scalars(select(WealthTaskORM).where(
                WealthTaskORM.user_id==user_id,WealthTaskORM.context_id==context_id,WealthTaskORM.status.not_in(TERMINAL))))
            if any(t.control_mode!='AGENT' or t.status=='PAUSED' or t.data.get('stop_new_submissions') is True or t.data.get('session_closed') is True for t in tasks):
                raise CandidateError('TASK_WRITE_BLOCKED',409)
            grant['can_edit']=True # Actual service query proved no applicable control lock.
        return grant

class JointPortfolio:
    def __init__(self,root,*,data_version=None,daily_version=None,bank_factory=None):
        from ...integrations.wealth_bank_api import HttpWealthBankApi
        self.bank_factory=bank_factory or HttpWealthBankApi
        self.ready=False
        self.root=Path(root);self.data_version=data_version or os.getenv('PORTFOLIO_DATA_VERSION',DATA)
        self.daily_version=daily_version or os.getenv('PORTFOLIO_DAILY_VERSION',DAILY)
        from ...domain.wealth_bank_portfolio_bundle import FILE,versions
        augmented=(self.root/FILE).is_file() and self.data_version==DATA and self.daily_version==DAILY
        if augmented:self.data_version,self.daily_version=versions(DATA,DAILY,self.root)
        self.store=MemoryPortfolioStore(lambda:datetime.now(timezone.utc));self.sessions={};self.candidate_contexts={}
        import asyncio
        self.lock=asyncio.Lock()
        from ...domain.wealth_portfolio_order import CodeCategorySelector
        self.selector=CodeCategorySelector()
        def load(version):
            from ...domain.wealth_portfolio_candidates import ProductIndex
            try:
                snapshot=read(self.root,self.root/'.deps/wealth_portfolio_data/snapshots',DATA if augmented and version==self.data_version else version)
                if augmented and version==self.data_version:
                    from ...domain.wealth_bank_portfolio_bundle import augment
                    snapshot=augment(snapshot,self.root)
                return ProductIndex(snapshot,immutable_source=True)
            except (OSError,ValueError) as error:raise CandidateError('PORTFOLIO_DATA_UNAVAILABLE',503) from error
        self.indexes=SnapshotIndexCache(load)
        self.indexes.async_load=True
    async def preload(self):
        """Validate the immutable source once at startup; no bank/user caching."""
        import asyncio
        import time,logging
        started=time.perf_counter()
        index=await asyncio.to_thread(self.indexes.get,self.data_version)
        if index.daily_version!=self.daily_version:raise CandidateError('DAILY_VERSION_MISMATCH')
        import json
        from collections import Counter
        runtime=json.loads((self.root/'config/wealth/competition_runtime_scope.json').read_text(encoding='utf8'))
        counts=Counter(p.execution_policy['category'] for p in index.products.values() if p.execution_policy and p.execution_policy.get('category') in runtime['categories'])
        if dict(counts)!=runtime['categories']:raise CandidateError('COMPETITION_POOL_COUNT_MISMATCH',503)
        # The final bank's immutable policy registry is static material too.
        # Validate/load it before readiness, without any user-specific Plan.
        from ...domain.wealth_paper_policy import registered_policy_reasons
        sample=next((p for p in index.products.values() if p.execution_policy),None)
        if sample:
            reasons=await asyncio.to_thread(registered_policy_reasons,sample.execution_policy,sample.product_id)
            if reasons:raise CandidateError(reasons[0],503)
        # Finish collecting the discarded full daily graph during startup,
        # rather than making the first user calculation pay that cost.
        import gc
        await asyncio.to_thread(gc.collect)
        elapsed=time.perf_counter()-started
        self.ready=True
        logging.getLogger(__name__).info('Frozen material ready: %.3fs; directory=%d; policy-bound=%d',elapsed,len(index.products),len(index.policy_references))
        return {'data_version':index.version,'directory':len(index.products),
                'initialization_seconds':self.indexes.last_load_seconds,
                'total_preload_seconds':elapsed,
                'interned_unknown_fields':index.interned_unknown_fields}
    def bind(self,db,user_id,bank):
        facts=JointFacts(ServerReads(db,user_id,bank,self.indexes.cache.get(self.data_version)),self.data_version,self.daily_version)
        candidates=CandidateService(facts,self.indexes,self.selector);candidates.sessions=self.sessions;candidates.lock=self.lock
        service=PortfolioLifecycle(PortfolioModelProvider(candidates,facts),self.store,JointAccess(facts,self.store))
        service.candidate_contexts=self.candidate_contexts
        return facts,service
    async def snapshot(self,db,user_id,context_id,bank):
        facts,_=self.bind(db,user_id,bank);result=build_snapshot(await facts.read(user_id))
        if result.context_id!=context_id:raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        return result.model_dump(mode='json')
    async def owned_plan(self,db,user_id,portfolio_id):
        record=await invoke(self.store.read(portfolio_id,user_id))
        facts,service=self.bind(db,user_id,self.bank_factory())
        codes={r['product_id'] for r in record.metadata['validation_contract']['selected_references']}
        codes.update(r.product_id for r in record.plan.advised_positions)
        facts.transport.product_ids=sorted(codes)
        return (await invoke(service.get_for_execution(user_id,portfolio_id,record.plan.context_id))).model_dump(mode='json')
    async def plan(self,*,db,user_id,context_id,bank,params):
        _,service=self.bind(db,user_id,bank)
        return (await invoke(service.create(user_id,PortfolioCreate(context_id=context_id,**{k:v for k,v in params.items() if v is not None})))).model_dump(mode='json')
    async def recommend(self,*,db,user_id,context_id,bank,params):
        _,service=self.bind(db,user_id,bank)
        p={k:v for k,v in params.items() if k!='selected_family_ids' and v is not None}
        # B sends the last server-issued cursor; its bound session is resolved server-side.
        if p.get('cursor') and not p.get('candidate_binding'):
            matching=[s for s in self.sessions.values() if s.user_id==user_id and s.cursor==p['cursor']]
            if len(matching)!=1:raise CandidateError('PORTFOLIO_CANDIDATE_BINDING_STALE')
            state=matching[0];p['candidate_binding']={'session_id':state.session_id,'expected_version':state.version,'filter':state.filter.model_dump()}
        group=await invoke(service.recommend(user_id,PortfolioRecommendation(context_id=context_id,**p)))
        codes=sorted({s.product_id for card in group.items for s in card.share_classes})
        return group.model_dump(mode='json')
    async def revise(self,*,db,user_id,context_id,bank,params):
        _,service=self.bind(db,user_id,bank);selection=PortfolioSelection.model_validate(params['selection'])
        return (await invoke(service.revise(user_id,selection.portfolio_id,selection))).model_dump(mode='json')

def install(app,root):
    provider=JointPortfolio(root)
    app.state.wealth_portfolio_source=provider
    app.state.wealth_action_handlers={'plan_portfolio':provider.plan,'recommend_products':provider.recommend,'revise_portfolio':provider.revise}
    return provider
