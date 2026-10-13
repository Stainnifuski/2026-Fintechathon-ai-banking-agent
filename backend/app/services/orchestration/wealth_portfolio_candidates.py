"""服务端候选/选择/A-C/换批状态；不注册中央应用、不接交易。"""
import asyncio
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from typing import Protocol,Callable
from pydantic import ValidationError
from ...schemas.wealth_portfolio_candidates import (CurrentFacts,CandidateFilter,SelectionRequest,SelectionDecision,
    ShareReference,SessionSelectionRequest,SwitchRequest,NextBatchRequest,SessionAction)
from ...domain.wealth_portfolio_eligibility import qualify_purchase,version_context,assessment_reasons
from ...domain.wealth_portfolio_candidates import ProductIndex,pipeline,reliable_family
from .wealth_portfolio_data import read

class CandidateError(ValueError):
    def __init__(self,code: str,status_code: int=409,details=None):
        super().__init__(code);self.code=code;self.status_code=status_code;self.details=details

class CandidateFactsSource(Protocol):
    async def read_current(self,user_id: str,at: datetime) -> CurrentFacts:
        """读取当前正式评估及银行份额规则，按at核实最新版本；不得接受客户端事实。"""
        ...

class FamilySelector(Protocol):
    version: str
    fixture_only: bool
    def select(self,pool: list,excluded: set[str],limit: int) -> list[str]: ...

class SnapshotIndexCache:
    """资料版本注入加载；最多保留两个版本，选择不重复解析完整目录。"""
    def __init__(self,loader: Callable[[str],ProductIndex]):
        self.loader=loader;self.cache={};self.loads=0;self.last_load_seconds=0.0
        import threading
        self.load_lock=threading.RLock()
    def get(self,version: str) -> ProductIndex:
        with self.load_lock:return self._get(version)
    def _get(self,version: str) -> ProductIndex:
        if version not in self.cache:
            start=time.monotonic();index=self.loader(version)
            if index.version!=version:raise CandidateError('DATA_VERSION_MISMATCH')
            self.loads+=1;self.last_load_seconds=time.monotonic()-start
            if len(self.cache)>=2:self.cache.pop(next(iter(self.cache)))
            self.cache[version]=index
        return self.cache[version]
    @classmethod
    def local(cls,root: Path):
        return cls(lambda version:ProductIndex(read(root,root/'.deps/wealth_portfolio_data/snapshots',version)))

@dataclass
class CandidateSession:
    session_id: str
    user_id: str
    filter: CandidateFilter
    context: object
    pool_version: str
    version: int=1
    displayed: set[str]=field(default_factory=set)
    selected: dict[str,dict]=field(default_factory=dict)
    cursor: str | None=None
    requires_refresh: bool=False

class CandidateService:
    def __init__(self,source: CandidateFactsSource | None,indexes: SnapshotIndexCache,selector: FamilySelector | None=None,
                 *,enable_fixture: bool=False,clock: Callable[[],datetime] | None=None):
        self.source=source;self.indexes=indexes;self.selector=selector;self.enable_fixture=enable_fixture
        self.clock=clock or (lambda:datetime.now(timezone.utc));self.sessions={};self.lock=asyncio.Lock()

    async def current(self,user_id: str):
        if self.source is None:raise CandidateError('ASSESSMENT_SOURCE_UNAVAILABLE',503)
        now=self.clock()
        try:facts=CurrentFacts.model_validate(await self.source.read_current(user_id,now))
        except CandidateError:raise
        except (ValidationError,ValueError,KeyError) as error:raise CandidateError('CURRENT_FACTS_INVALID',409) from error
        if facts.user_id!=user_id:raise CandidateError('FACT_SCOPE_FORBIDDEN',403)
        if facts.mode=='fixture' and not self.enable_fixture:raise CandidateError('FIXTURE_SOURCE_FORBIDDEN',403)
        if getattr(self.source,'completed_read_time',False):
            if facts.read_at>self.clock():raise CandidateError('CURRENT_FACT_FUTURE_TIME',409)
            now=facts.read_at
        index=await asyncio.to_thread(self.indexes.get,facts.data_version) if getattr(self.indexes,'async_load',False) else self.indexes.get(facts.data_version)
        if index.daily_version!=facts.daily_version:raise CandidateError('DAILY_VERSION_MISMATCH')
        return facts,index,now

    def validate_current(self,request: SelectionRequest,facts: CurrentFacts,index: ProductIndex,user_id: str,now: datetime) -> SelectionDecision:
        context=version_context(facts)
        if request.expected_context!=context:raise CandidateError('QUALIFICATION_CONTEXT_STALE')
        decisions=[];failures={};seen_codes=set();seen_families={}
        for item in request.items:
            product=index.products.get(item.product_id);decision=qualify_purchase(product,facts,user_id,now,item,context)
            reasons=decision.reason_codes[:]
            if item.product_id in seen_codes:reasons.append('DUPLICATE_SELECTED_SHARE')
            seen_codes.add(item.product_id)
            family,problems=reliable_family(product,now) if product else (None,[])
            reasons+=problems
            if family in seen_families:
                reasons.append('DUPLICATE_SELECTED_FAMILY');failures.setdefault(seen_families[family],[]).append('DUPLICATE_SELECTED_FAMILY')
            if family:seen_families[family]=item.product_id
            if reasons:failures.setdefault(item.product_id,[]).extend(reasons)
            decisions.append(decision)
        failures={code:list(dict.fromkeys(reasons)) for code,reasons in failures.items()}
        return SelectionDecision(accepted=not failures,reason_codes=sorted({r for reasons in failures.values() for r in reasons}),decisions=decisions,failures=failures,context=context)

    async def validate_selection(self,user_id: str,request: SelectionRequest) -> SelectionDecision:
        facts,index,now=await self.current(user_id)
        return self.validate_current(request,facts,index,user_id,now)

    def _state(self,user_id: str,request: SessionAction) -> CandidateSession:
        state=self.sessions.get(request.session_id)
        if state is None:raise CandidateError('CANDIDATE_SESSION_NOT_FOUND',404)
        if state.user_id!=user_id:raise CandidateError('CANDIDATE_SESSION_FORBIDDEN',403)
        if state.version!=request.expected_version:raise CandidateError('CANDIDATE_SESSION_VERSION_STALE')
        return state

    def _revalidate_selected(self,state: CandidateSession,facts: CurrentFacts,index: ProductIndex,now: datetime):
        context=version_context(facts)
        for code,entry in state.selected.items():
            request=SelectionRequest(expected_context=context,items=[ShareReference.model_validate(entry['reference'])])
            decision=self.validate_current(request,facts,index,state.user_id,now)
            entry['valid']=decision.accepted;entry['reason_codes']=decision.failures.get(code,[])

    async def _current_for_state(self,state: CandidateSession):
        try:return await self.current(state.user_id)
        except CandidateError as error:
            for entry in state.selected.values():entry['valid']=False;entry['reason_codes']=[error.code]
            state.requires_refresh=True;state.cursor=None
            raise CandidateError(error.code,error.status_code,self._view(state)) from error

    def _check_binding(self,state: CandidateSession,facts: CurrentFacts,index: ProductIndex,now: datetime):
        self._revalidate_selected(state,facts,index,now)
        if state.context!=version_context(facts) or state.requires_refresh or assessment_reasons(facts,state.user_id,now):
            state.requires_refresh=True;state.cursor=None
            raise CandidateError('CANDIDATE_CONTEXT_STALE',409,self._view(state))

    @staticmethod
    def _view(state: CandidateSession) -> dict:
        return {'session_id':state.session_id,'version':state.version,'context':state.context.model_dump(mode='json'),
            'pool_version':state.pool_version,'selected':deepcopy(state.selected),'displayed_family_ids':sorted(state.displayed),
            'cursor':state.cursor,'requires_refresh':state.requires_refresh}

    def _batch(self,state: CandidateSession,result,facts: CurrentFacts):
        if self.selector is None:return {'status':'unavailable','reason_codes':['SELECTOR_UNAVAILABLE'],'items':[],'algorithm_status':'not_connected'}
        if self.selector.fixture_only and (facts.mode!='fixture' or not self.enable_fixture):
            return {'status':'unavailable','reason_codes':['FIXTURE_SELECTOR_FORBIDDEN'],'items':[],'algorithm_status':'not_connected'}
        excluded=state.displayed | {entry['family_id'] for entry in state.selected.values()}
        pool={f.family_id:f for f in result.family_candidates};remaining=set(pool)-excluded
        try:selected=self.selector.select(result.family_candidates,excluded,3)
        except ValueError as error:raise CandidateError(str(error),422) from error
        if len(selected)>3 or len(set(selected))!=len(selected) or not set(selected)<=remaining:raise CandidateError('SELECTOR_OUTPUT_INVALID')
        state.displayed.update(selected)
        state.cursor=secrets.token_urlsafe(24) if remaining-set(selected) else None
        return {'status':'ok' if selected else 'no_eligible_products','reason_codes':[] if selected else ['POOL_EXHAUSTED'],
            'pool_exhausted':state.cursor is None,'items':[pool[f].model_dump(mode='json') for f in selected],'algorithm_status':'fixture_only' if self.selector.fixture_only else self.selector.version}

    async def candidates(self,user_id: str,filter: CandidateFilter) -> dict:
        async with self.lock:
            facts,index,now=await self.current(user_id)
            try:result=pipeline(index,filter,facts,user_id,now)
            except ValueError as error:raise CandidateError(str(error),422) from error
            base={'pipeline':result.model_dump(mode='json'),'source_kind':facts.mode,'fixture_only':facts.mode=='fixture'}
            if result.status!='ok':return {**base,'status':result.status,'reason_codes':result.reason_codes,'items':[],'session':None}
            state=CandidateSession(secrets.token_urlsafe(24),user_id,filter,result.context,result.pool_version)
            batch=self._batch(state,result,facts)
            if batch['status']=='unavailable':return {**base,**batch,'session':None}
            self.sessions[state.session_id]=state
            return {**base,**batch,'session':self._view(state)}

    async def next_batch(self,user_id: str,request: NextBatchRequest) -> dict:
        async with self.lock:
            state=self._state(user_id,request);facts,index,now=await self._current_for_state(state)
            self._check_binding(state,facts,index,now)
            result=pipeline(index,state.filter,facts,user_id,now)
            if result.pool_version!=state.pool_version:state.requires_refresh=True;state.cursor=None;raise CandidateError('CANDIDATE_POOL_STALE',409,self._view(state))
            if state.cursor is None:return {'status':'no_eligible_products','reason_codes':['POOL_EXHAUSTED'],'pool_exhausted':True,'items':[],'session':self._view(state)}
            if request.cursor!=state.cursor:raise CandidateError('CANDIDATE_CURSOR_STALE')
            batch=self._batch(state,result,facts);state.version+=1
            return {**batch,'session':self._view(state),'pipeline_stages':result.stages}

    async def select(self,user_id: str,request: SessionSelectionRequest) -> dict:
        async with self.lock:
            state=self._state(user_id,request);facts,index,now=await self._current_for_state(state);self._check_binding(state,facts,index,now)
            combined=[ShareReference.model_validate(entry['reference']) for entry in state.selected.values()]+request.items
            decision=self.validate_current(SelectionRequest(expected_context=version_context(facts),items=combined),facts,index,user_id,now)
            if decision.accepted:
                for item in request.items:
                    family,_=reliable_family(index.products[item.product_id],now)
                    state.selected[item.product_id]={'reference':item.model_dump(mode='json'),'family_id':family,'valid':True,'reason_codes':[]}
                state.version+=1
            return {'selection':decision.model_dump(mode='json'),'session':self._view(state)}

    async def switch_share(self,user_id: str,request: SwitchRequest) -> dict:
        async with self.lock:
            state=self._state(user_id,request);facts,index,now=await self._current_for_state(state);self._check_binding(state,facts,index,now)
            previous=[code for code,entry in state.selected.items() if entry['family_id']==request.family_id]
            if len(previous)!=1:raise CandidateError('SELECTED_FAMILY_NOT_FOUND')
            target=index.products.get(request.target.product_id)
            family,_=reliable_family(target,now) if target else (None,[])
            if family!=request.family_id:raise CandidateError('SHARE_FAMILY_MISMATCH')
            old=index.products.get(previous[0])
            if not old or old.share_class not in ('A','C') or not target or target.share_class not in ('A','C'):raise CandidateError('AC_SWITCH_NOT_SUPPORTED')
            items=[ShareReference.model_validate(entry['reference']) for code,entry in state.selected.items() if code!=previous[0]]+[request.target]
            decision=self.validate_current(SelectionRequest(expected_context=version_context(facts),items=items),facts,index,user_id,now)
            if decision.accepted:
                state.selected.pop(previous[0]);state.selected[request.target.product_id]={'reference':request.target.model_dump(mode='json'),'family_id':family,'valid':True,'reason_codes':[]};state.version+=1
            return {'selection':decision.model_dump(mode='json'),'session':self._view(state)}

    async def refresh(self,user_id: str,request: SessionAction) -> dict:
        async with self.lock:
            state=self._state(user_id,request);facts,index,now=await self._current_for_state(state);self._revalidate_selected(state,facts,index,now)
            result=pipeline(index,state.filter,facts,user_id,now)
            state.context=result.context;state.pool_version=result.pool_version;state.requires_refresh=False;state.displayed=set();state.version+=1
            batch=self._batch(state,result,facts) if result.status=='ok' else {'status':result.status,'reason_codes':result.reason_codes,'items':[]}
            return {**batch,'session':self._view(state),'pipeline':result.model_dump(mode='json')}

