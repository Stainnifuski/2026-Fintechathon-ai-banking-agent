"""B2: recorded A outputs, real simulated bank and grouped personal MFA."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import pyotp
import pytest
from tests.integration.test_wealth_bank import store, http_store, assess, command, authorize, move
from backend.app.models import UserORM
from backend.app.models.wealth import WealthTaskORM, WealthTaskNodeORM
from backend.app.services.bank import wealth_service as bank
from backend.app.services.orchestration import wealth_plan, wealth_task_worker
from backend.app.integrations.wealth_authorization import WealthPolicyError
from sqlalchemy import select


class RecordedSource:
    """Test adapter, only returns explicit targets. No production registration."""
    def __init__(self, client):
        self.client, self.plan, self.owner = client, None, 'U1'

    async def owned_plan(self, db, user_id, portfolio_id):
        if user_id != self.owner or not self.plan or portfolio_id != self.plan['portfolio_id']:
            raise WealthPolicyError('方案不存在', 'NOT_FOUND', 404)
        return deepcopy(self.plan)

    async def snapshot(self, db, user_id, context_id, bank_api):
        facts = await bank_api.assets(user_id)
        cash = [a for a in facts['cash'] if a['account_type'] != 'credit']
        positions = {}
        for lot in facts['lots']:
            key = (lot['account_id'], lot['product_id'], int(lot['version_id'].split(':')[1]))
            p = positions.setdefault(key, dict(position_key=':'.join(map(str,key)), account_id=key[0], product_id=key[1], product_version=key[2],
                lot_ids=[], units='0.0000', redeemable_units='0.0000', market_value_fen=0,
                bucket='stable' if lot['category']=='bond' else 'cash_management' if lot['category']=='cash' else 'growth', nav=lot['nav'], nav_date=lot['nav_date']))
            p['lot_ids'].append(lot['lot_id'])
            p['units'] = format(Decimal(p['units'])+Decimal(lot['units']), '.4f')
            p['redeemable_units'] = format(Decimal(p['redeemable_units'])+Decimal(lot['redeemable_units']), '.4f')
            p['market_value_fen'] += lot['market_value_fen']
        basis = sum(a['balance_fen'] for a in cash)+sum(p['market_value_fen'] for p in positions.values())+facts['receivable_fen']
        snap = dict(schema_version='wealth-asset-snapshot-v1', context_id=facts['context_id'], asset_scope_id='isolated-U1', account_ids=[a['account_id'] for a in cash],
            accounts=cash, positions=list(positions.values()), receivable_fen=facts['receivable_fen'], basis_fen=basis,
            total_assets_fen=basis, valuation_complete=facts['valuation_complete'])
        snap.update(orders=facts['orders'],context_epoch=None,as_of=facts['as_of'],business_at=facts['business_at'],basis_kind='linked_bank_assets',basis_status='fixture_only',assessment=None,data_versions={'fixture':'recorded-consumer-only'},missing_fields=[])
        snap['snapshot_id'] = hashlib.sha256(json.dumps({k:v for k,v in snap.items() if k!='as_of'},sort_keys=True).encode()).hexdigest()
        return snap

    async def record(self, local, entries, *, version=9):
        snap = await self.snapshot(None, 'U1', 'legacy-v1', local)
        products = {p['product_id']:p for p in await local.products('U1')}
        selected = [dict(account_id='A1', product_id=pid, product_version=1, disclosure_version=1,
            family_id=pid, share_class=None, bucket=bucket, target_ratio_total=ratio,
            target_fen=int(Decimal(snap['basis_fen'])*Decimal(ratio))) for pid,bucket,ratio in entries]
        retained = [p for p in snap['positions'] if p['product_id'] not in {i['product_id'] for i in selected}]
        residual = snap['basis_fen']-sum(i['target_fen'] for i in selected)-sum(p['market_value_fen'] for p in retained)
        self.plan = dict(schema_version='wealth-portfolio-plan-v1',portfolio_id='isolated-portfolio',version=version,
            snapshot_id=snap['snapshot_id'],context_id='legacy-v1',asset_scope_id=snap['asset_scope_id'],
            basis_fen=snap['basis_fen'],basis_status='confirmed',model_status='fixture',selected_positions=selected,
            cash_residual_fen=residual,missing_fields=[],buckets=[])
        for bucket in ('cash_management','stable','growth'):
            amount = sum(i['target_fen'] for i in selected if i['bucket']==bucket)+sum(p['market_value_fen'] for p in retained if p['bucket']==bucket)+(residual if bucket=='cash_management' else 0)
            self.plan['buckets'].append(dict(bucket=bucket,min_ratio='0',max_ratio='1',target_ratio=str(Decimal(amount)/snap['basis_fen']),target_fen=amount,current_fen=0))
        assert all(pid in products for pid,_,_ in entries)
        return self.plan


SEVEN = [('MW001','cash_management','0.20'), ('MW002','stable','0.10'), ('MF002','stable','0.10'), ('MW003','stable','0.10'),
         ('MF003','growth','0.20'), ('MF004','growth','0.15'), ('MF005','growth','0.15')]


async def setup(store, http_store, monkeypatch):
    from backend.app.services.ai.llm import LLMService
    async def forbid(*args,**kwargs):
        pytest.fail('B2 must not call LLM')
    monkeypatch.setattr(LLMService,'_generate',forbid)
    client, local, app = http_store
    secret = pyotp.random_base32()
    async with store() as db:
        await assess(db)
        user = await db.get(UserORM,'U1'); user.totp_secret, user.totp_enabled = secret, True
        await db.commit()
    source = RecordedSource(client); app.state.wealth_portfolio_source = source
    return client, local, source, secret


async def prepare(client, source):
    return await client.post('/api/v1/wealth/portfolio/plans/isolated-portfolio/prepare',json={
        'expected_version':source.plan['version'],'snapshot_id':source.plan['snapshot_id']})


@pytest.mark.asyncio
async def test_revision_during_bank_precheck_cannot_save_stale_execution(store,http_store,monkeypatch):
    client,local,source,_ = await setup(store,http_store,monkeypatch)
    await source.record(local,SEVEN)
    original = local.precheck
    changed = False

    async def revise_during_precheck(*args, **kwargs):
        nonlocal changed
        result = await original(*args, **kwargs)
        if not changed:
            source.plan['version'] += 1
            changed = True
        return result

    monkeypatch.setattr(local,'precheck',revise_during_precheck)
    response = await prepare(client,source)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'STALE_PORTFOLIO'
    from backend.app.models.wealth import WealthPlanORM
    async with store() as db:
        assert list(await db.scalars(select(WealthPlanORM))) == []
    facts = await local.assets('U1')
    assert facts['orders'] == []
    assert all(a['held_fen']==0 for a in facts['cash'])


async def confirm_and_verify(client, mapping, secret):
    base = '/api/v1/wealth/plans/'+mapping['execution_plan_id']
    preview = await client.post(base+'/preview',json={'version':mapping['execution_plan_version']})
    assert preview.status_code == 200, preview.text
    views = preview.json()
    if len(views['items'])==7:
        incomplete=await client.post(base+'/confirm',json={'version':views['version'],'items':[
            dict(authorization_id=i['authorization_id'],digest=i['digest'],disclosure_version=i['command']['disclosure_version'],accepted=True) for i in views['items'][:6]]})
        assert incomplete.json()['detail']['code']=='STALE_PLAN'
    confirm = await client.post(base+'/confirm',json={'version':views['version'],'items':[
        dict(authorization_id=i['authorization_id'],digest=i['digest'],disclosure_version=i['command']['disclosure_version'],accepted=True) for i in views['items']]})
    assert confirm.status_code == 200, confirm.text
    verify = await client.post(base+'/verify',json={'version':views['version'],'proof':pyotp.TOTP(secret).now()})
    assert verify.status_code == 200, verify.text
    return base, views


@pytest.mark.asyncio
async def test_f11_seven_targets_one_confirmation_one_mfa_atomic_hold(store,http_store,monkeypatch):
    client,local,source,secret = await setup(store,http_store,monkeypatch)
    await source.record(local,SEVEN)
    before = (await client.get('/api/v1/wealth/assets')).json()['cash']
    response = await prepare(client,source)
    assert response.status_code == 200, response.text
    mapping = response.json()
    assert mapping['portfolio_version']==9 and mapping['execution_plan_version']==1
    assert len(mapping['actions'])==7 and len(mapping['execution_plan']['items'])==7
    repeated = await prepare(client,source)
    assert repeated.json()['execution_plan_id']==mapping['execution_plan_id']
    assert (await client.get('/api/v1/wealth/assets')).json()['cash']==before
    base = '/api/v1/wealth/plans/'+mapping['execution_plan_id']
    assert (await client.post(base+'/execute',json={'version':1})).json()['detail']['code']=='AUTHORIZATION_REQUIRED'
    _,views = await confirm_and_verify(client,mapping,secret)
    assert len(views['items'])==7
    accepted = await client.post(base+'/execute',json={'version':1})
    assert accepted.status_code==200,accepted.text
    assert all(i['status']=='ACCEPTED' for i in accepted.json()['items'])
    after = (await client.get('/api/v1/wealth/assets')).json()['cash'][0]
    assert after['balance_fen']==before[0]['balance_fen']
    assert after['held_fen']==2_000_000 and after['available_fen']==0
    retry = await client.post(base+'/execute',json={'version':1})
    assert [i['command']['operation_id'] for i in retry.json()['items']]==[i['command']['operation_id'] for i in accepted.json()['items']]
    for item in accepted.json()['items']:
        order=item['bank_result']
        cancel=await client.post('/api/v1/wealth/orders/'+order['operation_id']+'/cancel',json={
            'digest':order['digest'],'disclosure_version':1,'accepted':True})
        assert cancel.status_code==200,cancel.text
    assert (await client.get('/api/v1/wealth/assets')).json()['cash']==before


@pytest.mark.asyncio
async def test_revision_invalidates_old_execution_without_legacy_budget_override(store,http_store,monkeypatch):
    client,local,source,_ = await setup(store,http_store,monkeypatch)
    await source.record(local,[('MF002','stable','0.25')])
    first = (await prepare(client,source)).json()
    base='/api/v1/wealth/plans/'+first['execution_plan_id']
    preview = (await client.post(base+'/preview',json={'version':1})).json()
    source.plan['version']=10
    source.plan['selected_positions'][0]['target_ratio_total']='0.30';source.plan['selected_positions'][0]['target_fen']=600000
    source.plan['cash_residual_fen']=1400000
    for b in source.plan['buckets']:
        if b['bucket']=='stable':b['target_ratio'],b['target_fen']='0.30',600000
        if b['bucket']=='cash_management':b['target_ratio'],b['target_fen']='0.70',1400000
    second = await prepare(client,source)
    assert second.status_code==200,second.text
    assert second.json()['execution_plan_id']==first['execution_plan_id'] and second.json()['execution_plan_version']==2
    assert (await client.post(base+'/confirm',json={'version':1,'items':[dict(authorization_id=i['authorization_id'],digest=i['digest'],disclosure_version=1,accepted=True) for i in preview['items']]})).json()['detail']['code']=='STALE_PLAN'
    assert (await client.post(base+'/adopt',json={'version':2,'items':[]})).json()['detail']['code']=='PORTFOLIO_REVISION_REQUIRED'
    old_input=dict(version=2,account_id='A1',budget_fen=100000,horizon_days=365)
    assert (await client.put(base,json=old_input)).json()['detail']['code']=='PORTFOLIO_REVISION_REQUIRED'


@pytest.mark.asyncio
async def test_scope_stale_snapshot_unknown_fields_and_human_control(store,http_store,monkeypatch):
    client,local,source,_ = await setup(store,http_store,monkeypatch)
    await source.record(local,[('MF002','stable','0.25')])
    route='/api/v1/wealth/portfolio/plans/isolated-portfolio/prepare'
    body={'expected_version':9,'snapshot_id':source.plan['snapshot_id']}
    assert (await client.post(route,json={**body,'subject_user_id':'U2'})).status_code==422
    assert (await client.post(route,json={**body,'expected_version':8})).json()['detail']['code']=='STALE_PORTFOLIO'
    assert (await client.post(route,json={**body,'snapshot_id':'old'})).json()['detail']['code']=='STALE_SNAPSHOT'
    source.owner='U2'
    assert (await prepare(client,source)).status_code==404
    source.owner='U1'
    source.plan['context_id']='other'
    assert (await prepare(client,source)).json()['detail']['code']=='STALE_SNAPSHOT'
    source.plan['context_id']='legacy-v1'
    async with store() as db:
        db.add(WealthTaskORM(task_id='human-test',user_id='U1',session_id='test-session',context_id='legacy-v1',account_id='A1',version=1,status='WAITING',control_mode='HUMAN',owner_id='operator',data={}))
        await db.commit()
    response=await prepare(client,source)
    assert response.json()['detail']['code']=='TASK_WRITE_BLOCKED',response.text


@pytest.mark.asyncio
async def test_existing_position_is_subtracted_and_sell_buy_uses_settlement_gate(store,http_store,monkeypatch):
    client,local,source,secret = await setup(store,http_store,monkeypatch)
    async with store() as db:
        buy=command('seed-current-holding',amount=500000)
        aid=await authorize(db,buy)
        await bank.accept_many(db,'U1',[(buy,aid)])
        await move(db,12); await bank.settle(db,buy.operation_id)
    plan=await source.record(local,[('MF002','stable','0.30')])
    snap=await source.snapshot(None,'U1','legacy-v1',local)
    response=await prepare(client,source)
    assert response.status_code==200,response.text
    assert response.json()['actions'][0]['amount_fen']==plan['selected_positions'][0]['target_fen']-snap['positions'][0]['market_value_fen']
    # Explicit A revision reduces the existing product and adds a growth target.
    await source.record(local,[('MF002','stable','0.10'),('MF004','growth','0.30')],version=10)
    sell=await prepare(client,source)
    assert sell.status_code==200,sell.text
    mapping=sell.json()
    assert mapping['stage']=='SELL' and len(mapping['execution_plan']['items'])==1
    assert next(a for a in mapping['actions'] if a['direction']=='purchase')['status']=='WAITING_SETTLEMENT_AND_REREAD_CASH'
    base,_=await confirm_and_verify(client,mapping,secret)
    accepted=await client.post(base+'/execute',json={'version':mapping['execution_plan_version']})
    assert accepted.status_code==200,accepted.text
    async with store() as db:
        task=await db.scalar(select(WealthTaskORM).where(WealthTaskORM.user_id=='U1'))
        nodes=list(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task.task_id)))
        assert {'SETTLEMENT','REREAD_CASH','BUY_PLAN'} <= {n.kind for n in nodes}
        task_id=task.task_id
        await move(db,40)
        await bank.settle(db,accepted.json()['items'][0]['command']['operation_id'])
    await client.get(base+'/progress')
    await source.record(local,[('MF004','growth','0.30')],version=11)
    blocked=await prepare(client,source)
    assert blocked.json()['detail']['code']=='RECONCILIATION_REQUIRED',blocked.text
    async with store() as db:
        for _ in range(24):await wealth_task_worker.tick(db,task_id,local)
    purchase=await prepare(client,source)
    assert purchase.status_code==200,purchase.text
    assert purchase.json()['stage']=='BUY' and purchase.json()['execution_plan_version']==3
    assert purchase.json()['execution_plan']['history'][0]['stage']=='SELL'
    assert (await client.post(base+'/execute',json={'version':3})).json()['detail']['code']=='AUTHORIZATION_REQUIRED'


@pytest.mark.asyncio
async def test_no_public_fixture_or_client_plan_bypass(http_store):
    client,_,app=http_store
    if hasattr(app.state,'wealth_portfolio_source'):del app.state.wealth_portfolio_source
    response=await client.post('/api/v1/wealth/portfolio/plans/fake/prepare',json={'expected_version':1,'snapshot_id':'fake'})
    assert response.status_code==503 and response.json()['detail']['code']=='PORTFOLIO_PROVIDER_UNAVAILABLE'


def test_unadopted_advice_cannot_become_an_implicit_trade():
    from backend.app.services.orchestration.wealth_selection import execution_targets
    from backend.app.integrations.wealth_authorization import WealthPolicyError
    plan={'selected_positions':[], 'advised_positions':[{'product_id':'MF002','product_version':1,'target_fen':10000,'current_fen':0}]}
    with pytest.raises(WealthPolicyError) as error:
        execution_targets(plan,{'accounts':[]})
    assert error.value.code=='PORTFOLIO_SELECTION_REQUIRED'
    assert '校验所选产品与比例' in error.value.message
