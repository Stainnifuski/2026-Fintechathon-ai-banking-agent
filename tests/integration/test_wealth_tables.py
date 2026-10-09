"""B3a bank facts, publication/version scope and protected cancellation."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from tests.integration.test_wealth_bank import store,http_store,assess,command,authorize,move
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthProductORM,WealthProductVersionORM,WealthNavORM,WealthTaskORM
from backend.app.models.wealth_research import WealthContextORM,WealthContextSelectionORM,WealthDatasetORM
from backend.app.services.bank import wealth_service as bank


async def accepted(store,key='table-order',product='MF002'):
    cmd=command(key,product=product)
    async with store() as db:
        await assess(db)
        auth=await authorize(db,cmd)
        return (await bank.accept_many(db,'U1',[(cmd,auth)]))[0]


@pytest.mark.asyncio
async def test_http_assets_include_full_accounts_orders_and_bound_metadata(store,http_store):
    client,_,_=http_store
    await accepted(store,'first-lot');await accepted(store,'second-lot')
    async with store() as db:
        db.add(AccountORM(account_id='A3',user_id='U1',account_type='savings',card_number='mock',balance_fen=100000,daily_transfer_limit_fen=5000000))
        await move(db,15);await db.commit()
    async with store() as db:await bank.settle(db,'first-lot')
    async with store() as db:await bank.settle(db,'second-lot')
    facts=(await client.get('/api/v1/wealth/assets')).json()
    assert {a['account_id'] for a in facts['cash']}=={'A1','A3'}
    assert len(facts['orders'])==2 and len(facts['lots'])==2
    for row in [*facts['orders'],*facts['lots']]:
        assert row['product_id']=='MF002' and row['product_version']==1
        assert row['disclosure_version']==1 and row['daily_metric']['status']=='ok'
        assert row['daily_metric']['date']>row['daily_metric']['previous_date']
    assert all(row['version_id']=='MF002:1' for row in facts['lots'])
    assert not any(row['account_id']=='A2' for row in facts['orders'])


@pytest.mark.asyncio
async def test_future_publications_and_money_products_do_not_fake_daily_change(store,http_store):
    client,_,_=http_store
    await accepted(store,'nav-bond');await accepted(store,'nav-money','MF001')
    async with store() as db:
        rows=list(await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id=='MF002:1')))
        for row in rows:
            row.available_at=datetime(2027,1,1,tzinfo=timezone.utc)
        # Exactly one published observation, regardless of future rows.
        row=min(rows,key=lambda row:row.nav_date);row.available_at=datetime(2026,1,1,tzinfo=timezone.utc)
        expected_date=row.nav_date.isoformat()
        await db.commit()
    facts=(await client.get('/api/v1/wealth/assets')).json()
    bond=next(o for o in facts['orders'] if o['product_id']=='MF002')['daily_metric']
    money=next(o for o in facts['orders'] if o['product_id']=='MF001')['daily_metric']
    assert bond['status']=='unavailable' and bond['value_percent'] is None
    assert bond['date']==expected_date
    assert money['status']=='not_applicable' and money['value_percent'] is None


@pytest.mark.asyncio
async def test_old_order_metadata_and_details_keep_original_product_version(store,http_store):
    client,_,_=http_store
    await accepted(store)
    async with store() as db:
        old=await db.get(WealthProductVersionORM,'MF002:1')
        db.add(WealthProductVersionORM(version_id='MF002:2',product_id=old.product_id,version=2,name='New C share metadata',kind=old.kind,
            category=old.category,risk_level=old.risk_level,rules={**deepcopy(old.rules),'share_class':'C'},disclosure={**deepcopy(old.disclosure),'version':2},created_at=old.created_at))
        product=await db.get(WealthProductORM,'MF002');product.current_version=2;await db.commit()
    facts=(await client.get('/api/v1/wealth/assets')).json();row=facts['orders'][0]
    assert row['product_version']==1 and row['disclosure_version']==1 and row['share_class'] is None
    detail=await client.get('/api/v1/wealth/products/MF002/detail?product_version=1')
    assert detail.status_code==200,detail.text
    assert detail.json()['product_version']==1 and detail.json()['name']==row['product_name']
    assert (await client.get('/api/v1/wealth/products/MF002/detail?product_version=99')).status_code==404


@pytest.mark.asyncio
async def test_cancel_requires_confirmation_and_restores_only_held_cash(store,http_store):
    client,_,_=http_store;order=await accepted(store)
    before=(await client.get('/api/v1/wealth/assets')).json()
    eligibility=(await client.get('/api/v1/wealth/orders/table-order/cancellation-eligibility')).json()
    assert eligibility['eligibility']=='ALLOWED'
    body=dict(digest=order['digest'],disclosure_version=1,accepted=False)
    assert (await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)).status_code==422
    body.update(accepted=True,digest='0'*64)
    assert (await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)).status_code==409
    body.update(digest=order['digest'],disclosure_version=99)
    assert (await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)).status_code==409
    body['disclosure_version']=1;cancel=await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)
    assert cancel.status_code==200 and cancel.json()['status']=='CANCELLED'
    after=(await client.get('/api/v1/wealth/assets')).json()
    assert before['cash'][0]['balance_fen']==after['cash'][0]['balance_fen']
    assert before['cash'][0]['held_fen']-after['cash'][0]['held_fen']==100000
    assert (await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)).status_code==200


@pytest.mark.asyncio
@pytest.mark.parametrize('control_mode,status',[('HUMAN','WAITING'),('AGENT','PAUSED')])
async def test_old_context_cancel_and_human_control_cannot_release_holds(store,http_store,control_mode,status):
    client,_,_=http_store;order=await accepted(store)
    body=dict(digest=order['digest'],disclosure_version=1,accepted=True)
    async with store() as db:
        db.add(WealthTaskORM(task_id='human-table-task',user_id='U1',session_id='test',account_id='A1',context_id='legacy-v1',version=1,status=status,control_mode=control_mode,owner_id='operator' if control_mode=='HUMAN' else None,data={}))
        await db.commit()
    response=await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)
    assert response.status_code==409 and response.json()['detail']['code']=='TASK_WRITE_BLOCKED'
    async with store() as db:
        task=await db.get(WealthTaskORM,'human-table-task');task.status='COMPLETED'
        db.add(WealthDatasetORM(dataset_key='table:test',dataset_id='table',version='test',mode='synthetic',manifest={},ingested_at=datetime.now(timezone.utc)))
        db.add(AccountORM(account_id='A3',user_id='U1',account_type='savings',card_number='mock',balance_fen=100000,daily_transfer_limit_fen=5000000))
        await db.flush()
        db.add(WealthContextORM(context_id='other-table-context',user_id='U1',account_id='A3',dataset_key='table:test',version=1,data={'disposable':True,'business_at':'2026-09-30T06:00:00+00:00'}))
        db.add(WealthContextSelectionORM(user_id='U1',context_id='other-table-context',version=2));await db.commit()
    response=await client.post('/api/v1/wealth/orders/table-order/cancel',json=body)
    assert response.status_code==409 and response.json()['detail']['code']=='ACCOUNT_CONTEXT_MISMATCH',response.text
    async with store() as db:
        assert await bank.held_cash(db,'A1')==100000
