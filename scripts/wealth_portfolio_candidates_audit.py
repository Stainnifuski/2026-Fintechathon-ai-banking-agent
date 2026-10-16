"""离线完整资料流水线核验；正式source缺失单列，fixture用户上下文明确标记。"""
import argparse
import asyncio
import json
import socket
import time
from collections import Counter
from pathlib import Path
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.schemas.wealth_portfolio_data import DataSnapshot
from backend.app.domain.wealth_portfolio_candidates import ProductIndex,pipeline
from backend.app.domain.wealth_portfolio_eligibility import qualify_purchase
from backend.app.services.orchestration.wealth_portfolio_candidates import SnapshotIndexCache,CandidateService,CandidateError
from backend.app.services.orchestration.wealth_portfolio_data import read
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,NOW,service,share,FixtureSource


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--version',required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];out=root/'.deps/wealth_portfolio_candidates';out.mkdir(exist_ok=True)
    def forbidden(*args,**kwargs):raise RuntimeError('NETWORK_FORBIDDEN')
    socket.socket=forbidden;socket.create_connection=forbidden
    cache=SnapshotIndexCache.local(root);start=time.monotonic();index=cache.get(args.version);first=time.monotonic()-start
    start=time.monotonic();assert cache.get(args.version) is index;reuse=time.monotonic()-start
    formal=CandidateService(None,cache)
    # source缺失在读任何用户事实之前503；这里同步检查current协程，未启动网络。
    coroutine=formal.current('not-a-real-user')
    try:coroutine.send(None)
    except CandidateError as error:formal_error={'code':error.code,'status_code':error.status_code}
    _,facts=fixture_pool(1);facts.data_version=index.version;facts.daily_version=index.daily_version;facts.offers={}
    audit={'source_status':formal_error,'assessment_context':'explicit_fixture_only_not_current_bank_assessment',
        'data_version':index.version,'daily_version':index.daily_version,'first_load_seconds':round(first,6),'reuse_seconds':round(reuse,6),
        'index_loads':cache.loads,'external_requests':0,'by_source':{},'theme_rules':{},'bank_policy':[]}
    ledger=[]
    for kind in ('real','simulated','fixture'):
        subset=[p for p in index.products.values() if p.source_kind==kind]
        # 同一版本的全索引复用，仅本次统计显式按来源分列。
        view=object.__new__(ProductIndex);view.__dict__=index.__dict__.copy();view.scope_codes=[p.product_id for p in subset if p.scope_status=='verified']
        view.products={p.product_id:p for p in subset}
        start=time.monotonic();result=pipeline(view,CandidateFilter(mode='general'),facts,'fixture-user',NOW)
        audit['by_source'][kind]={'directory':len(subset),'status':result.status,'stages':result.stages,'reason_counts':dict(Counter(r for d in result.decisions for r in d.reason_codes)),
            'elapsed_seconds':round(time.monotonic()-start,6),'current_purchase_qualified':result.stages['purchase_qualified_shares']}
        ledger += [{'source_kind':kind,'product_id':d.product_id,'user_context':'fixture_only','reason_codes':d.reason_codes,'user_level':d.user_level,'product_level':d.product_level,'purchase_eligible':d.purchase_eligible} for d in result.decisions]
        tags=[t for code in view.scope_codes for t in view.tags[code]]
        audit['theme_rules'][kind]={'tagged_products':len({t.product_id for t in tags}),'by_tag':dict(Counter(t.tag for t in tags)),
            'actual_example':tags[0].model_dump(mode='json') if tags else None}
    for p in index.products.values():
        if p.source_kind=='simulated' and p.product_id.startswith('B'):
            audit['bank_policy'].append({'code':p.product_id,'original_risk':p.fields['raw_risk'].value,'scope':p.scope_status,'reason':p.scope_reason})
    reader=FixtureSource(facts);handler=CandidateService(reader,cache,enable_fixture=True,clock=lambda:NOW)
    times=[]
    for _ in range(2):
        started=time.monotonic();call=handler.candidates('fixture-user',CandidateFilter(mode='general'))
        try:call.send(None)
        except StopIteration as result:stages=result.value['pipeline']['stages']
        times.append(round(time.monotonic()-started,6))
    audit['actual_handler_fixture_context']={'first_seconds':times[0],'reused_seconds':times[1],'source_reads':reader.calls,'index_loads':cache.loads,'stages':stages,'label':'explicit_fixture_assessment_no_bank_offers; not_formal_user_verification'}
    (out/'actual_pool_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False),encoding='utf8')
    (out/'actual_pool_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
