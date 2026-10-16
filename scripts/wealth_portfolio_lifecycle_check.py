"""显式隔离生命周期证据入口；不接正式账户、不产生交易。"""
import argparse
import asyncio
import json
import socket
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import field
from backend.app.schemas.wealth_portfolio import PortfolioSelection,SelectionItem
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate
from backend.app.services.orchestration.wealth_portfolio_data import digest
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError

def key(plan) -> dict:
    return {'portfolio_id':plan.portfolio_id,'version':plan.version,'snapshot_id':plan.snapshot_id,'basis_status':plan.basis_status,'basis_fen':plan.basis_fen,
        'bucket_fen':[b.target_fen for b in plan.buckets],'growth_ratio_ex_cash':plan.growth_ratio_ex_cash,'cash_residual_fen':plan.cash_residual_fen,
        'selected_positions':[p.model_dump() for p in plan.selected_positions], 'advised_positions':[r.model_dump() for r in plan.advised_positions]}

def request(plan,**values):
    return PortfolioSelection(**({'portfolio_id':plan.portfolio_id,'expected_version':plan.version,'snapshot_id':plan.snapshot_id,'context_id':plan.context_id,'items':plan.selected_positions}|values))

async def check() -> dict:
    s,*_=lifecycle_fixture();p=await s.create('fixture-user',PortfolioCreate(context_id='fixture-context',growth_ratio_ex_cash='.625'))
    result={'create':key(p),'get_identical':(await s.get('fixture-user',p.portfolio_id,p.context_id))==p};original=deepcopy(s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation'])
    p=await s.revise('fixture-user',p.portfolio_id,request(p,growth_ratio_ex_cash='.626'));result['adjustment']=key(p)
    result['original_U_preserved']=s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation']==original
    p=await s.revise('fixture-user',p.portfolio_id,request(p,revision_kind='preference',growth_ratio_ex_cash='.65'));result['preference']=key(p)
    result['new_U_created']=s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation']['b']!=original['b']
    r2,data,facts,*_=lifecycle_fixture(2);next(r for r in data.products if r.product_id=='Q07C').fields['formal_risk']=field(4,'risk_level');facts.offers['Q07C'].risk_level=4
    old=await r2.create('fixture-user',PortfolioCreate(context_id='fixture-context'));before=digest(key(old))
    try:await r2.revise('fixture-user',old.portfolio_id,request(old,items=[SelectionItem(product_id='Q07C',product_version=1,share_class='C',target_ratio_total='0')]))
    except CandidateError as error:result['risk_rejection']={'code':error.code,'status':error.status_code,'reasons':error.details,'before':before,'after':digest(key((await r2.store.read(old.portfolio_id,'fixture-user')).plan))}
    result['original_model']=original;result['external_requests']=0;result['bank_writes']=0;return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',action='store_true');args=parser.parse_args()
    if not args.fixture:parser.error('Explicit --fixture required; formal sources remain unregistered')
    def forbidden(*args,**kwargs):raise RuntimeError('external connections disabled')
    async def offline_check():
        original_connect=socket.socket.connect;socket.socket.connect=forbidden
        try:return await check()
        finally:socket.socket.connect=original_connect
    out=Path('.deps/wealth_portfolio_lifecycle');out.mkdir(parents=True,exist_ok=True);result=asyncio.run(offline_check())
    (out/'lifecycle_evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'create_version':result['create']['version'],'adjust_version':result['adjustment']['version'],'preference_version':result['preference']['version'],
        'original_U_preserved':result['original_U_preserved'],'risk_atomic':result['risk_rejection']['before']==result['risk_rejection']['after'],'external_requests':0,'bank_writes':0}))
