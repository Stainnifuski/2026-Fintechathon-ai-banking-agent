"""Current instant-bank flow with independent SELL and BUY consent/MFA.

Recorded allocation is a test adapter; all preview, MFA, bank execution and
continuation reconciliation use production code and disposable databases.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthProductVersionORM,WealthOrderORM,WealthCashHoldORM,WealthTaskORM
from backend.app.integrations.wealth_authorization import WealthPolicyError
from backend.app.domain.wealth_incremental_planning import incremental_targets
from backend.app.services.orchestration.wealth_planning_continuation import assert_redemption_effects
from tests.integration.test_wealth_bank import store,http_store,command,authorize
from tests.integration.test_wealth_instant import setup,bound
from tests.integration.test_wealth_portfolio_prepare import RecordedSource
from tests.integration.wealth_mfa_helpers import sms_verify
from backend.app.services.bank import wealth_service


class IncrementalSource(RecordedSource):
    def __init__(self,client):
        super().__init__(client);self.plans={};self.continuation_lock=asyncio.Lock();self.resume_count=0

    async def owned_plan(self,db,user_id,portfolio_id):
        if user_id!='U1' or portfolio_id not in self.plans:raise WealthPolicyError('方案不存在','NOT_FOUND',404)
        return deepcopy(self.plans[portfolio_id])

    async def record_incremental(self,bank,reserve=0):
        snap=await self.snapshot(None,'U1',None,bank)
        total=snap['basis_fen'];available=sum(a['available_fen'] for a in snap['accounts'])
        held=[sum(p['market_value_fen'] for p in snap['positions'] if p['bucket']==b) for b in ('cash_management','stable','growth')]
        result=incremental_targets(3,total,held,reserve,available,0,[total//5,total*3//5,total-total//5-total*3//5],'.015')
        # Only one old growth product and one selected bond, both real bank fixtures.
        products={p['product_id']:p for p in await bank.products('U1')}
        rows=[]
        for code,bucket,target in [('MF003','growth',result['targets'][2]),('MF002','stable',result['targets'][1])]:
            old=next((p for p in snap['positions'] if p['product_id']==code),None)
            rows.append(dict(product_id=code,product_version=1,disclosure_version=1,share_class=None,original_share_class=None,
                family_id=code,name=products[code]['name'],bucket=bucket,target_fen=target,current_fen=old['market_value_fen'] if old else 0,
                role='holding' if old else 'selected',account_ids=['A1'] if old else [],holding_cap_fen=None,fee_status='unknown',market_status='unknown'))
        # For this consumer fixture the remaining liquid allocation is cash,
        # rather than a cash-management product. It has no financial permission.
        pid='recorded-incremental-'+str(len(self.plans)+1)
        plan=dict(portfolio_id=pid,version=1,context_id=snap['context_id'],snapshot_id=snap['snapshot_id'],asset_scope_id=snap['asset_scope_id'],
            basis_fen=total,basis_status='fixture_only',missing_fields=[],cash_residual_fen=result['targets'][0],
            selected_positions=[dict(product_id='MF002',product_version=1,share_class=None,target_ratio_total=str(Decimal(rows[1]['target_fen'])/total))],
            advised_positions=rows,reasons=['PLANNING_INCREMENTAL_V2','PLANNING_CASH_BUDGET_V1'],
            planning={'reserve_fen':reserve,'redemption_totals_fen':result['sales']},
            buckets=[dict(bucket=b,current_fen=0,target_fen=v,target_ratio=str(Decimal(v)/total),min_ratio='0',max_ratio='1') for b,v in zip(('cash_management','stable','growth'),result['targets'])])
        self.plans[pid]=plan
        return plan

    async def resume_planning(self,db,user_id,previous,bank):
        self.resume_count+=1
        return await self.record_incremental(bank,previous['planning']['reserve_fen'])


async def initialize(store,http_store,monkeypatch):
    client,bank,app=http_store
    async with store() as db:
        await setup(db,monkeypatch,'MF003')
        bond=await db.get(WealthProductVersionORM,'MF002:1')
        bond.rules={**bond.rules,'dataset_id':'shared-test','dataset_version':'v1'}
        await db.commit()
        buy=await bound(db,command('old-growth',product='MF003',amount=1_600_000))
        assert (await wealth_service.accept_many(db,'U1',[(buy,await authorize(db,buy))]))[0]['status']=='SETTLED'
    source=IncrementalSource(client);app.state.wealth_portfolio_source=source
    return client,bank,source,await source.record_incremental(bank)


async def prepare(client,plan):
    response=await client.post(f"/api/v1/wealth/portfolio/plans/{plan['portfolio_id']}/prepare",json={'expected_version':1,'snapshot_id':plan['snapshot_id'],'payment_account_id':'A1'})
    assert response.status_code==200,response.text
    return response.json()


async def confirm_and_verify(client,prepared):
    base='/api/v1/wealth/plans/'+prepared['execution_plan_id'];version=prepared['execution_plan_version']
    preview=await client.post(base+'/preview',json={'version':version});assert preview.status_code==200,preview.text
    view=preview.json()
    response=await client.post(base+'/confirm',json={'version':version,'items':[{'authorization_id':i['authorization_id'],'digest':i['digest'],'disclosure_version':i['command']['disclosure_version'],'accepted':True} for i in view['items']]})
    assert response.status_code==200,response.text
    response=await sms_verify(client,base,version=version,reference=view['verification_reference_id'])
    assert response.status_code==200,response.text
    return base,view['verification_reference_id']


@pytest.mark.asyncio
async def test_two_phases_require_two_independent_confirmations_and_mfa(store,http_store,monkeypatch):
    client,bank,source,plan=await initialize(store,http_store,monkeypatch)
    before=await bank.assets('U1');first=await prepare(client,plan)
    assert first['stage']=='SELL'
    assert (await bank.assets('U1'))['cash']==before['cash']
    base='/api/v1/wealth/plans/'+first['execution_plan_id']
    premature=await client.post(base+'/continue-purchase',json={'version':1})
    assert premature.status_code==409 and premature.json()['detail']['code']=='PLANNING_REDEMPTION_NOT_SETTLED'
    base,first_reference=await confirm_and_verify(client,first)
    executed=await client.post(base+'/execute',json={'version':1});assert executed.status_code==200,executed.text
    assert all(i['status']=='SETTLED' for i in executed.json()['items'])
    continued=await client.post(base+'/continue-purchase',json={'version':1});assert continued.status_code==200,continued.text
    second=continued.json()['prepared'];assert second['stage']=='BUY'
    assert second['execution_plan_id']!=first['execution_plan_id']
    second_base='/api/v1/wealth/plans/'+second['execution_plan_id']
    denied=await client.post(second_base+'/execute',json={'version':1});assert denied.status_code in (403,409),denied.text
    replay=await client.post(base+'/continue-purchase',json={'version':1})
    assert replay.json()==continued.json() and source.resume_count==1
    second_base,second_reference=await confirm_and_verify(client,second)
    assert first_reference!=second_reference
    executed=await client.post(second_base+'/execute',json={'version':1});assert executed.status_code==200,executed.text
    assert all(i['status']=='SETTLED' for i in executed.json()['items'])
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.status!='SETTLED'))==0


@pytest.mark.asyncio
async def test_unrelated_cash_change_after_sell_blocks_buy_and_preserves_cash(store,http_store,monkeypatch):
    client,bank,source,plan=await initialize(store,http_store,monkeypatch)
    first=await prepare(client,plan);base,_=await confirm_and_verify(client,first)
    response=await client.post(base+'/execute',json={'version':1});assert response.status_code==200,response.text
    async with store() as db:
        account=await db.get(AccountORM,'A1');account.balance_fen+=1;await db.commit()
    response=await client.post(base+'/continue-purchase',json={'version':1})
    assert response.status_code==409 and response.json()['detail']['code']=='PLANNING_EXTRA_ASSET_CHANGE'
    assert source.resume_count==0


def test_unknown_redemption_cannot_be_treated_as_cash():
    with pytest.raises(WealthPolicyError,match='尚未全部核实'):
        assert_redemption_effects({'cash':[],'lots':[]},{},[{'command':{'direction':'redeem'},'bank_result':{'status':'UNKNOWN'}}])


@pytest.mark.asyncio
@pytest.mark.parametrize('guard',['unknown','paused','foreign'])
async def test_continuation_honors_reconciliation_pause_and_ownership(store,http_store,monkeypatch,guard):
    client,bank,source,plan=await initialize(store,http_store,monkeypatch)
    first=await prepare(client,plan);base,_=await confirm_and_verify(client,first)
    response=await client.post(base+'/execute',json={'version':1});assert response.status_code==200,response.text
    if guard=='unknown':
        query=bank.query
        async def unknown(*args,**kwargs):
            result=await query(*args,**kwargs);return {**result,'status':'UNKNOWN'}
        monkeypatch.setattr(bank,'query',unknown)
        expected='PLANNING_REDEMPTION_NOT_SETTLED'
    elif guard=='paused':
        async with store() as db:
            task=await db.scalar(select(WealthTaskORM).where(WealthTaskORM.user_id=='U1'))
            task.status='PAUSED';await db.commit()
        expected='TASK_WRITE_BLOCKED'
    else:
        from backend.app.middleware.auth import get_current_user
        http_store[2].dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
        expected='PLAN_NOT_FOUND'
    response=await client.post(base+'/continue-purchase',json={'version':1})
    assert response.status_code in (404,409),response.text
    if guard!='foreign':assert response.json()['detail']['code']==expected,response.text
    assert source.resume_count==0
