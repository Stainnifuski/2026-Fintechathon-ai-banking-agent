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
        raise WealthPolicyError(str(error),error.code,getattr(error,'status_code',422)) from error

DATA='38005c79535cb83a2d5be663d8a7a1812ba21946fe8f9fa2ab21ca80aea338a9'
DAILY='e5ce972368c9b735a5fe9ddd64fa0fd81a9194c419cb89ceda255ead71f460d0'

class ServerReads:
    def __init__(self,db,user,bank):self.db,self.user,self.bank=db,user,bank
    async def get(self,path):
        if path=='/api/v1/auth/me':return {'user_id':self.user}
        suffix=path.removeprefix('/api/v1/wealth/')
        if suffix=='assets':return await self.bank.assets(self.user)
        if suffix=='products':return await self.bank.products(self.user)
        if suffix=='contexts':return await self.bank.call('POST','/contexts',{'subject_user_id':self.user})
        if suffix=='assessments/current':
            row=await latest_assessment(self.db,self.user)
            return assessment_view(row) if row else None
        raise CandidateError('BANK_READ_PATH_DENIED',403)

class JointFacts(BankPortfolioFacts):
    """Use B's full summary and explicit lot versions, never the legacy run adapter."""
    completed_read_time=True
    async def read(self,user_id):
        if user_id!=self.transport.user:raise SnapshotError('subject mismatch','FACT_SCOPE_FORBIDDEN',403)
        raw=await self.transport.bank.assets(user_id)
        if 'asset_summary' not in raw:raise SnapshotError('shared summary unavailable','ASSET_SUMMARY_UNAVAILABLE',503)
        assessment=await self.transport.get('/api/v1/wealth/assessments/current')
        contexts=await self.transport.get('/api/v1/wealth/contexts')
        products=await self.transport.bank.products(user_id)
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
            'data_versions':{'data_version':self.data_version,'daily_version':self.daily_version,'offers_version':'adapter:'+digest(business_content(products))}}
    async def read_current(self,user_id,at):
        facts=await super().read_current(user_id,at)
        profile=await self.transport.db.get(WealthProfileORM,user_id)
        row=await latest_assessment(self.transport.db,user_id)
        facts.assessment_current_proven=bool(profile and row and row.profile_version==profile.version)
        facts.offers_version='adapter:'+digest(business_content(self.observations['products']))
        completed=datetime.now(timezone.utc)
        facts.read_at=completed
        if facts.assessment:facts.assessment.read_at=completed
        for offer in facts.offers.values():
            if offer.current_simulation:offer.current_simulation.observed_at=completed
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
        self.root=Path(root);self.data_version=data_version or os.getenv('PORTFOLIO_DATA_VERSION',DATA)
        self.daily_version=daily_version or os.getenv('PORTFOLIO_DAILY_VERSION',DAILY)
        self.store=MemoryPortfolioStore(lambda:datetime.now(timezone.utc));self.sessions={};self.candidate_contexts={}
        import asyncio
        self.lock=asyncio.Lock()
        from ...domain.wealth_portfolio_dpp import DppSelector
        self.selector=DppSelector()
        def load(version):
            from ...domain.wealth_portfolio_candidates import ProductIndex
            try:return ProductIndex(read(self.root,self.root/'.deps/wealth_portfolio_data/snapshots',version),immutable_source=True)
            except (OSError,ValueError) as error:raise CandidateError('PORTFOLIO_DATA_UNAVAILABLE',503) from error
        self.indexes=SnapshotIndexCache(load)
        self.indexes.async_load=True
    async def preload(self):
        """Validate the immutable source once at startup; no bank/user caching."""
        import asyncio
        index=await asyncio.to_thread(self.indexes.get,self.data_version)
        if index.daily_version!=self.daily_version:raise CandidateError('DAILY_VERSION_MISMATCH')
        # Finish collecting the discarded full daily graph during startup,
        # rather than making the first user calculation pay that cost.
        import gc
        await asyncio.to_thread(gc.collect)
        return {'data_version':index.version,'directory':len(index.products),
                'initialization_seconds':self.indexes.last_load_seconds,
                'interned_unknown_fields':index.interned_unknown_fields}
    def bind(self,db,user_id,bank):
        facts=JointFacts(ServerReads(db,user_id,bank),self.data_version,self.daily_version)
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
        _,service=self.bind(db,user_id,self.bank_factory())
        return (await invoke(service.get(user_id,portfolio_id,record.plan.context_id))).model_dump(mode='json')
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
        return (await invoke(service.recommend(user_id,PortfolioRecommendation(context_id=context_id,**p)))).model_dump(mode='json')
    async def revise(self,*,db,user_id,context_id,bank,params):
        _,service=self.bind(db,user_id,bank);selection=PortfolioSelection.model_validate(params['selection'])
        return (await invoke(service.revise(user_id,selection.portfolio_id,selection))).model_dump(mode='json')

def install(app,root):
    provider=JointPortfolio(root)
    app.state.wealth_portfolio_source=provider
    app.state.wealth_action_handlers={'plan_portfolio':provider.plan,'recommend_products':provider.recommend,'revise_portfolio':provider.revise}
    return provider
