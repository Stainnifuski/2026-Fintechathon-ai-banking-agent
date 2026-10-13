"""第04步可复现离线计算与同集合对照；正常入口不自动启用fixture。"""
import argparse
import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Coroutine, NoReturn

os.environ['LLM_MODE']='offline'
from backend.app.domain.wealth_portfolio_dpp import DppSelector,build_corpus
from backend.app.domain.wealth_portfolio_candidates import pipeline
from backend.app.domain.wealth_portfolio_allocation import allocate
from backend.app.domain.wealth_portfolio_evaluation import evaluate_selector,evaluate_weights,latency
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateService,SnapshotIndexCache,CandidateError
from tests.fixtures.wealth_portfolio_model_v1 import model_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import NOW,fixture_pool


def complete(coroutine: Coroutine) -> Any:
    """本离线fixture来源无异步I/O，拒绝意外挂起的网络任务。"""
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    coroutine.close();raise RuntimeError('OFFLINE_SOURCE_UNEXPECTED_IO')


def blocked(*args: Any,**kwargs: Any) -> NoReturn:raise RuntimeError('NETWORK_DISABLED')


def fixture_run() -> tuple[dict,dict]:
    start=time.perf_counter();provider,data,facts,b=model_fixture(4,True);f,index,now=complete(provider.candidates.current('fixture-user'));cold=time.perf_counter()-start
    r=pipeline(index,CandidateFilter(mode='general'),f,'fixture-user',now);pool=r.family_candidates
    start=time.perf_counter();corpus=build_corpus(pool);feature=time.perf_counter()-start
    output={'source_kind':'fixture','fixture_only':True,'n':len(pool),'dimension':corpus.dimension,'material_stages':r.stages,
            'DPP':evaluate_selector(corpus,'DPP',{p.family_id for p in pool},{p.family_id for p in pool},r.stages['family_groups']),
            'MMR':evaluate_selector(corpus,'MMR',{p.family_id for p in pool},{p.family_id for p in pool},r.stages['family_groups'])}
    result=complete(provider.plan('fixture-user'));plan=result.model_dump(mode='json')
    current={'__cash_asset__':sum(a['balance_fen'] for a in b['accounts'])+b['summary']['redemption_receivable_fen']}
    for pos in result.metadata['positions']:
        family=pos.get('family_id')
        if family:current[family]=current.get(family,0)+pos['position']['market_value_fen']
    output['weights']=evaluate_weights(result.metadata['product_projections'],current,result.plan.basis_fen,result.metadata['model'],None)
    timing=[];selection_times=[]
    for _ in range(20):
        t=time.perf_counter();complete(provider.plan('fixture-user'));timing.append(time.perf_counter()-t)
        t=time.perf_counter();provider.candidates.selector.select(pool,set(),3);selection_times.append(time.perf_counter()-t)
    output['latency']={'environment':'host Python '+os.sys.version.split()[0]+'; standard library; local memory fixture',
        'cold_index':latency([cold]),'feature_build':latency([feature]),'hot_provider':latency(timing),'warm_selection':latency(selection_times),'corpus_builds':provider.candidates.selector.builds,'index_loads':provider.candidates.indexes.loads}
    output['analytic_A']=allocate(4,1000000,[.2,.3,.5]);output['analytic_B']=allocate(4,1000000,[.2,.3,.5],p='.625')
    return output,plan


def actual_run(root: Path,version: str) -> dict:
    cache=SnapshotIndexCache.local(root);selector=DppSelector();engine=CandidateService(None,cache,selector)
    try:complete(engine.candidates('not_a_real_user',CandidateFilter(mode='general')))
    except CandidateError as error:gate={'code':error.code,'http_status':error.status_code}
    start=time.perf_counter();index=cache.get(version);cold=time.perf_counter()-start
    _,facts=fixture_pool(1);facts.data_version=index.version;facts.daily_version=index.daily_version;facts.offers={}
    output={'formal_gate':gate,'source_note':'pure_pipeline_uses_explicit_fixture_assessment_only_not_formal_user','data_version':index.version,'daily_version':index.daily_version,'by_source':{},'cold_index':latency([cold])}
    for kind in ('real','simulated','fixture'):
        # 全目录读取一次，按来源的匹配台账视图，不凑正向产品。
        view=object.__new__(type(index));view.__dict__=index.__dict__.copy();view.products={k:p for k,p in index.products.items() if p.source_kind==kind};view.scope_codes=[k for k in index.scope_codes if k in view.products]
        r=pipeline(view,CandidateFilter(mode='general'),facts,'fixture-user',NOW);pool=r.family_candidates
        from collections import Counter
        output['by_source'][kind]={'directory':len(view.products),'status':r.status,'stages':r.stages,'reason_counts':dict(Counter(reason for d in r.decisions for reason in d.reason_codes)),
            'DPP':evaluate_selector(build_corpus(pool),'DPP',set(),set(),r.stages['family_groups']),
            'MMR':evaluate_selector(build_corpus(pool),'MMR',set(),set(),r.stages['family_groups']),
            'HHI':None,'adjustment_proxy':None,'preference_error':None,'model_plan':None}
    return output


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',action='store_true');parser.add_argument('--actual-version');args=parser.parse_args()
    if not args.fixture and not args.actual_version:parser.error('explicit --fixture or --actual-version required; no implicit fixture source')
    socket.create_connection=blocked;socket.socket.connect=blocked;socket.socket.connect_ex=blocked
    root=Path(__file__).resolve().parents[1];out=root/'.deps/wealth_portfolio_model';out.mkdir(parents=True,exist_ok=True)
    evidence={'external_requests':0,'financial_effect_status':'not_evaluated'}
    if args.fixture:
        evidence['fixture'],plan=fixture_run();(out/'fixture_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.actual_version:evidence['actual']=actual_run(root,args.actual_version)
    (out/'evaluation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'evidence':str(out/'evaluation.json'),'fixture_n':evidence.get('fixture',{}).get('n'),'formal_gate':evidence.get('actual',{}).get('formal_gate'),'external_requests':0},ensure_ascii=False))

if __name__=='__main__':main()

