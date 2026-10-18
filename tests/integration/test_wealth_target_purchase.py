"""Explicit target products, single group authorization, atomic purchase ledger."""
import pytest
from sqlalchemy import select, func
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthProductVersionORM, WealthOrderORM, WealthLotORM, WealthCashHoldORM, WealthPlanORM
from backend.app.services.bank import wealth_service as bank
from tests.integration.test_wealth_bank import store, http_store
from tests.integration.test_wealth_instant import setup
from tests.integration.wealth_mfa_helpers import sms_verify


async def selected(store, monkeypatch):
    async with store() as db:
        await setup(db, monkeypatch)
        version = await db.get(WealthProductVersionORM, 'MF001:1')
        version.rules = {**version.rules, 'dataset_id':'shared-test', 'dataset_version':'v1'}
        await db.commit()
        facts = await bank.assets(db,'U1')
        return {'request_id':'target-purchase-request', 'context_id':facts['context_id'], 'items':[
            {'product_id':p,'product_version':1,'disclosure_version':1,'amount_fen':100000}
            for p in ('MF001','MF002')]}


async def confirm_verify(client, data):
    base = '/api/v1/wealth/plans/'+data['plan']['plan_id']; preview = data['preview']
    confirmations = [{'authorization_id':i['authorization_id'],'digest':i['digest'],
        'disclosure_version':i['command']['disclosure_version'],'accepted':True} for i in preview['items']]
    missing = await client.post(base+'/confirm', json={'version':1,'items':confirmations[:1]})
    assert missing.status_code == 409
    result = await client.post(base+'/confirm', json={'version':1,'items':confirmations})
    assert result.status_code == 200, result.text
    assert (await client.post(base+'/execute', json={'version':1})).status_code == 409
    verified = await sms_verify(client, base, version=1, reference=preview['verification_reference_id'])
    assert verified.status_code == 200, verified.text
    return base


@pytest.mark.asyncio
async def test_all_selected_single_mfa_no_prepare_orders_and_stable_replay(store, http_store, monkeypatch):
    payload = await selected(store, monkeypatch); client, _, _ = http_store
    response = await client.post('/api/v1/wealth/target-purchases/prepare',json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['plan']['manual_purchase'] and 'portfolio_id' not in data['plan']
    assert data['plan']['configuration']['investment_fen'] == 200000
    assert all(i['command']['account_id']=='A1' for i in data['preview']['items'])
    replay = await client.post('/api/v1/wealth/target-purchases/prepare',json=payload)
    assert replay.status_code == 200 and replay.json()['plan']['plan_id']==data['plan']['plan_id']
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0
        assert (await db.get(AccountORM,'A1')).balance_fen == 2000000
    base = await confirm_verify(client, data)
    executed = await client.post(base+'/execute',json={'version':1})
    assert executed.status_code == 200, executed.text
    assert executed.json()['status'] == 'COMPLETED'
    assert all(i['status']=='SETTLED' for i in executed.json()['items'])
    replay = await client.post('/api/v1/wealth/target-purchases/prepare',json=payload)
    assert replay.status_code == 200 and replay.json()['preview'] is None
    assert replay.json()['plan']['status'] == 'COMPLETED'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 2
        assert await db.scalar(select(func.count()).select_from(WealthLotORM)) == 2
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0
        assert (await db.get(AccountORM,'A1')).balance_fen < 2000000


@pytest.mark.asyncio
async def test_total_scope_credit_duplicates_and_same_request_changes_rejected(store,http_store,monkeypatch):
    payload = await selected(store,monkeypatch); client, _, _ = http_store
    url='/api/v1/wealth/target-purchases/prepare'
    assert (await client.post(url,json={**payload,'account_id':'A2'})).status_code == 422
    assert (await client.post(url,json={**payload,'context_id':'other'})).status_code == 409
    assert (await client.post(url,json={**payload,'items':[payload['items'][0]]*2})).status_code == 422
    huge={**payload,'items':[{**item,'amount_fen':1100000} for item in payload['items']]}
    assert (await client.post(url,json=huge)).status_code == 409
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthPlanORM)) == 0
        (await db.get(AccountORM,'A1')).account_type='credit';await db.commit()
    credit=await client.post(url,json=payload)
    assert credit.status_code == 409, credit.text
    async with store() as db:
        (await db.get(AccountORM,'A1')).account_type='savings';await db.commit()
    prepared=await client.post(url,json=payload)
    assert prepared.status_code == 200,prepared.text
    changed={**payload,'items':[{**item,'amount_fen':200000} for item in payload['items']]}
    assert (await client.post(url,json=changed)).status_code == 409


@pytest.mark.asyncio
async def test_later_insufficient_cash_rolls_back_all_selected_purchases(store,http_store,monkeypatch):
    payload = await selected(store,monkeypatch); client, _, _ = http_store
    prepared=await client.post('/api/v1/wealth/target-purchases/prepare',json=payload)
    assert prepared.status_code == 200,prepared.text
    base=await confirm_verify(client,prepared.json())
    async with store() as db:
        (await db.get(AccountORM,'A1')).balance_fen=150000;await db.commit()
    result=await client.post(base+'/execute',json={'version':1})
    assert result.status_code == 200,result.text
    assert result.json()['status']=='PAUSED'
    assert result.json()['last_error']['code']=='INSUFFICIENT_AVAILABLE_FUNDS'
    async with store() as db:
        assert (await db.get(AccountORM,'A1')).balance_fen==150000
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthLotORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
