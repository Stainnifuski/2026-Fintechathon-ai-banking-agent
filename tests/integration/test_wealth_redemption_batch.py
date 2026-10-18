"""One confirmation/MFA over explicit redemptions, real HTTP and isolated ledgers."""
import pytest
from sqlalchemy import select, func
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthLotORM, WealthOrderORM, WealthCashHoldORM, WealthUnitHoldORM, WealthProductVersionORM, WealthPlanORM
from backend.app.services.bank import wealth_service as bank
from tests.integration.test_wealth_bank import store, http_store, authorize, command, NOW
from tests.integration.test_wealth_instant import setup, bound
from tests.integration.wealth_mfa_helpers import sms_verify


async def holdings(store, monkeypatch):
    async with store() as db:
        await setup(db, monkeypatch)
        one = await db.get(WealthProductVersionORM, 'MF001:1')
        one.rules = {**one.rules, 'dataset_id':'shared-test', 'dataset_version':'v1'}
        await db.commit()
        for product in ('MF001', 'MF002'):
            cmd = await bound(db, command('batch-opening-'+product, product=product))
            await bank.accept_many(db, 'U1', [(cmd, await authorize(db, cmd))])
        for lot in await db.scalars(select(WealthLotORM)):
            lot.unlock_date = NOW.date()
        await db.commit()
        facts = await bank.assets(db, 'U1')
        return {'request_id':'batch-redemption-request', 'items':[
            {'context_id':facts['context_id'], 'position_key':f'A1:{p}:1', 'input_mode':'units', 'units':'10.0000'}
            for p in ('MF001', 'MF002')]}


@pytest.mark.asyncio
async def test_grouped_redemptions_one_confirmation_mfa_and_no_prepare_holds(store, http_store, monkeypatch):
    payload = await holdings(store, monkeypatch)
    client, _, _ = http_store
    async with store() as db:
        balance = (await db.get(AccountORM,'A1')).balance_fen
    response = await client.post('/api/v1/wealth/redemptions/prepare', json=payload)
    assert response.status_code == 200, response.text
    data = response.json(); plan, preview = data['plan'], data['preview']
    assert len(preview['items']) == 2
    assert {i['command']['plan_id'] for i in preview['items']} == {plan['plan_id']}
    repeated = await client.post('/api/v1/wealth/redemptions/prepare', json=payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['plan']['plan_id'] == plan['plan_id']
    assert [i['command']['operation_id'] for i in repeated.json()['preview']['items']] == [i['command']['operation_id'] for i in preview['items']]
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 2
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthUnitHoldORM)) == 0
        assert (await db.get(AccountORM,'A1')).balance_fen == balance
    base = '/api/v1/wealth/plans/'+plan['plan_id']
    assert (await client.post(base+'/execute', json={'version':1})).status_code == 409
    confirmations = [{'authorization_id':i['authorization_id'], 'digest':i['digest'],
        'disclosure_version':i['command']['disclosure_version'], 'accepted':True} for i in preview['items']]
    assert (await client.post(base+'/confirm', json={'version':1,'items':confirmations[:1]})).status_code == 409
    confirmed = await client.post(base+'/confirm', json={'version':1,'items':confirmations})
    assert confirmed.status_code == 200, confirmed.text
    assert (await client.post(base+'/execute', json={'version':1})).status_code == 409
    verified = await sms_verify(client, base, reference=preview['verification_reference_id'], version=1)
    assert verified.status_code == 200, verified.text
    executed = await client.post(base+'/execute', json={'version':1})
    assert executed.status_code == 200, executed.text
    assert executed.json()['status'] == 'COMPLETED'
    assert all(i['status']=='SETTLED' for i in executed.json()['items'])
    replay = await client.post('/api/v1/wealth/redemptions/prepare', json=payload)
    assert replay.status_code == 200 and replay.json()['plan']['status'] == 'COMPLETED'
    assert replay.json()['preview'] is None
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.direction=='redeem')) == 2
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.status.in_(['ACCEPTED','PENDING_PRICING','PENDING_CASH']))) == 0
        assert (await db.get(AccountORM,'A1')).balance_fen > balance


@pytest.mark.asyncio
async def test_batch_invalid_input_and_reused_request_cannot_expand_authority(store, http_store, monkeypatch):
    payload = await holdings(store, monkeypatch)
    client, _, _ = http_store
    duplicate = {**payload, 'items':[payload['items'][0],payload['items'][0]]}
    assert (await client.post('/api/v1/wealth/redemptions/prepare', json=duplicate)).status_code == 422
    invalid = {**payload, 'items':[payload['items'][0],{**payload['items'][1], 'units':'999999.0000'}]}
    assert (await client.post('/api/v1/wealth/redemptions/prepare', json=invalid)).status_code == 409
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthPlanORM)) == 0
    prepared = await client.post('/api/v1/wealth/redemptions/prepare', json=payload)
    assert prepared.status_code == 200, prepared.text
    changed = {**payload, 'items':[{**i,'units':'20.0000'} for i in payload['items']]}
    assert (await client.post('/api/v1/wealth/redemptions/prepare', json=changed)).status_code == 409


@pytest.mark.asyncio
async def test_handoff_after_group_verification_blocks_new_redemption(store, http_store, monkeypatch):
    from backend.app.models.wealth import WealthTaskORM
    payload = await holdings(store, monkeypatch)
    client, _, _ = http_store
    response = await client.post('/api/v1/wealth/redemptions/prepare', json=payload)
    assert response.status_code == 200, response.text
    data = response.json(); preview = data['preview']; base = '/api/v1/wealth/plans/'+data['plan']['plan_id']
    confirmations = [{'authorization_id':i['authorization_id'], 'digest':i['digest'],
        'disclosure_version':i['command']['disclosure_version'], 'accepted':True} for i in preview['items']]
    assert (await client.post(base+'/confirm', json={'version':1,'items':confirmations})).status_code == 200
    verified = await sms_verify(client, base, reference=preview['verification_reference_id'], version=1)
    assert verified.status_code == 200, verified.text
    async with store() as db:
        tasks = list(await db.scalars(select(WealthTaskORM).order_by(WealthTaskORM.task_id)))
        assert len(tasks) == 2
        for task in tasks:
            task.control_mode = 'HUMAN'; task.status = 'PAUSED'
        await db.commit()
    result = await client.post(base+'/execute', json={'version':1})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'PAUSED'
    assert result.json()['last_error']['code'] == 'TASK_WRITE_BLOCKED'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.direction=='redeem')) == 0
