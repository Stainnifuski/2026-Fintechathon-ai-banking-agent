"""B3b: read-only authoritative totals, isolated banks only."""
import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import update, delete, text, select, func

from backend.app.models import AccountORM
from backend.app.models.wealth import WealthOrderORM, WealthLotORM, WealthCashHoldORM, WealthNavORM, WealthProfileORM, WealthAuthorizationORM
from backend.app.models.wealth_research import WealthContextORM, WealthContextSelectionORM, WealthDatasetORM
from backend.app.middleware.auth import get_current_user, create_access_token
from backend.app.schemas.wealth_assets import AssetSummary
from backend.app.services.bank.wealth_asset_summary import read
from backend.app.services.bank import wealth_service as bank
from tests.integration.test_wealth_bank import store, http_store, NOW, assess, authorize, command, move


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    from backend.app.services.ai.llm.client import LLMService
    async def forbidden(*args, **kwargs):
        pytest.fail('AssetSummary must not call the model')
    monkeypatch.setattr(LLMService, '_generate', forbidden)


async def seed_f18(store):
    async with store() as db:
        (await db.get(AccountORM, 'A1')).balance_fen = 600_000
        db.add_all([
            AccountORM(account_id='A3', user_id='U1', account_type='checking', card_number='mock3', balance_fen=200_000),
            AccountORM(account_id='C1', user_id='U1', account_type='credit', card_number='mockc', balance_fen=9_000_000),
        ])
        def order(key, account, status, direction='purchase', details=None):
            return WealthOrderORM(operation_id=key, user_id='U1', account_id=account, version_id='MF002:1',
                direction=direction, amount_fen=100_000 if direction=='purchase' else 0, units_scaled=0,
                param_digest='isolated-fixture', authorization_id='isolated-fixture', plan_version=1, status=status,
                accepted_at=NOW, pricing_date=NOW.date(), confirm_date=NOW.date(), cash_date=NOW.date(), details=details or {})
        db.add_all([order('fixture-buy','A1','SETTLED'),order('fixture-hold','A1','ACCEPTED'),
                    order('fixture-receivable','A3','PENDING_CASH','redeem',{'actual':{'cash_fen':50_000}})])
        await db.flush()
        db.add(WealthLotORM(lot_id='fixture-lot', operation_id='fixture-buy', user_id='U1', account_id='A1',
            version_id='MF002:1', units_scaled=15_000_000, cost_fen=150_000, acquired_date=NOW.date(), unlock_date=NOW.date()))
        db.add(WealthCashHoldORM(operation_id='fixture-hold', account_id='A1', amount_fen=100_000, status='HELD'))
        await db.execute(update(WealthNavORM).where(WealthNavORM.version_id=='MF002:1').values(price='1.00000000'))
        await db.commit()


async def summary(store):
    async with store() as db:
        return await read(db, 'U1')


@pytest.mark.asyncio
async def test_public_total_full_scope_and_same_snapshot(store, http_store):
    await seed_f18(store)
    client, _, app = http_store
    result=await client.get('/api/v1/wealth/asset-summary?account_id=A2&user_id=U2')
    assert result.status_code==200, result.text
    dto=AssetSummary.model_validate(result.json()).model_dump(mode='json')
    assert dto['account_ids']==['A1','A3']
    assert (dto['cash_balance_fen'],dto['held_fen'],dto['available_cash_fen'])==(800_000,100_000,700_000)
    assert (dto['positions_market_value_fen'],dto['redemption_receivable_fen'],dto['total_assets_fen'])==(150_000,50_000,1_000_000)
    again=(await client.get('/api/v1/wealth/asset-summary')).json()
    embedded=(await client.get('/api/v1/wealth/assets')).json()['asset_summary']
    assert again['snapshot_id']==embedded['snapshot_id']==dto['snapshot_id']
    assert again['asset_scope_id']==embedded['asset_scope_id']==dto['asset_scope_id']
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
    other=(await client.get('/api/v1/wealth/asset-summary')).json()
    assert other['account_ids']==['A2'] and other['total_assets_fen']==2_000_000
    assert other['asset_scope_id']!=dto['asset_scope_id']
    app.dependency_overrides.pop(get_current_user)
    assert (await client.get('/api/v1/wealth/asset-summary')).status_code==401
    client.headers['Authorization']='Bearer '+create_access_token({'sub':'U1'})
    assert (await client.get('/api/v1/wealth/asset-summary?user_id=U2')).json()['snapshot_id']==dto['snapshot_id']
    client.headers['Authorization']='Bearer '+create_access_token({'sub':'U2'})
    assert (await client.get('/api/v1/wealth/asset-summary?account_id=A1')).json()['account_ids']==['A2']
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthProfileORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthAuthorizationORM))==0
        # A single-account diagnostic must not masquerade as the canonical total.
        assert 'asset_summary' not in await bank.assets(db,'U1','A1')


@pytest.mark.asyncio
async def test_missing_valuation_preserves_known_money_and_changes_snapshot(store):
    await seed_f18(store)
    complete=await summary(store)
    async with store() as db:
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id=='MF002:1'))
        await db.commit()
    missing=await summary(store)
    assert missing['asset_scope_id']==complete['asset_scope_id']
    assert missing['snapshot_id']!=complete['snapshot_id']
    assert missing['positions_market_value_fen'] is None and missing['total_assets_fen'] is None
    assert missing['valuation_complete'] is False
    assert missing['missing_fields']==['lots.fixture-lot.market_value_fen']
    assert missing['cash_balance_fen']==800_000 and missing['redemption_receivable_fen']==50_000


@pytest.mark.asyncio
async def test_purchase_reservation_cancel_and_settlement_are_not_double_counted(store):
    before=await summary(store)
    async with store() as db:
        await assess(db)
        cmd=command('summary-cancel')
        aid=await authorize(db,cmd)
        await bank.accept_many(db,'U1',[(cmd,aid)])
    reserved=await summary(store)
    assert reserved['total_assets_fen']==before['total_assets_fen']==2_000_000
    assert reserved['available_cash_fen']==1_900_000 and reserved['held_fen']==100_000
    assert reserved['snapshot_id']!=before['snapshot_id']
    async with store() as db:
        await bank.cancel(db,'U1',cmd.operation_id)
    cancelled=await summary(store)
    assert cancelled['held_fen']==0 and cancelled['available_cash_fen']==2_000_000
    async with store() as db:
        cmd=command('summary-settlement')
        aid=await authorize(db,cmd)
        await bank.accept_many(db,'U1',[(cmd,aid)])
        await move(db,12)
        await bank.settle(db,cmd.operation_id)
    settled=await summary(store)
    assert settled['held_fen']==0 and settled['cash_balance_fen']==1_900_000
    assert settled['positions_market_value_fen']>0
    assert settled['total_assets_fen']==settled['cash_balance_fen']+settled['positions_market_value_fen']
    assert settled['snapshot_id']!=cancelled['snapshot_id']
    async with store() as db:
        sell=command('summary-redemption',direction='redeem',amount=0,quantity='10.0000')
        aid=await authorize(db,sell)
        await bank.accept_many(db,'U1',[(sell,aid)])
        await move(db,13)
        await bank.settle(db,sell.operation_id)
    pending=await summary(store)
    assert pending['redemption_receivable_fen']>0 and pending['cash_balance_fen']==settled['cash_balance_fen']
    async with store() as db:
        await move(db,14)
        await bank.settle(db,sell.operation_id)
    paid=await summary(store)
    assert paid['redemption_receivable_fen']==0
    assert paid['cash_balance_fen']==pending['cash_balance_fen']+pending['redemption_receivable_fen']
    # The remaining position may change with the next day's published NAV.
    assert paid['total_assets_fen']-pending['total_assets_fen']==paid['positions_market_value_fen']-pending['positions_market_value_fen']


@pytest.mark.asyncio
async def test_context_scope_does_not_mix_runs_or_ordinary_accounts(store):
    async with store() as db:
        db.add(WealthDatasetORM(dataset_key='mock',dataset_id='mock',version='fixture',mode='mock',manifest={},ingested_at=NOW))
        db.add_all([AccountORM(account_id=f'S{i}',user_id='U1',account_type='savings',card_number=f's{i}',balance_fen=100_000*i) for i in (1,2,3)])
        await db.flush()
        for i in (1,2,3):
            db.add(WealthContextORM(context_id=f'context{i}',user_id='U1',account_id=f'S{i}',dataset_key='mock',
                data={'disposable':True,'run_id':'same' if i<3 else 'different','business_at':NOW.isoformat()}))
        db.add(WealthContextSelectionORM(user_id='U1',context_id='context1',version=1))
        await db.commit()
    selected=await summary(store)
    assert selected['account_ids']==['S1','S2'] and selected['total_assets_fen']==300_000
    async with store() as db:
        await db.execute(update(WealthContextSelectionORM).values(context_id='legacy-v1'))
        await db.commit()
    ordinary=await summary(store)
    assert ordinary['account_ids']==['A1'] and ordinary['total_assets_fen']==2_000_000
    assert ordinary['asset_scope_id']!=selected['asset_scope_id']


@pytest.mark.asyncio
async def test_reader_uses_one_database_snapshot_during_concurrent_change(store,monkeypatch):
    await seed_f18(store)
    async with store() as db:
        if db.get_bind().dialect.name=='sqlite':
            await db.execute(text('PRAGMA journal_mode=WAL'))
    ready, resume=asyncio.Event(),asyncio.Event()
    original=bank.held_cash
    async def pause(db,account_id):
        if not ready.is_set():
            ready.set()
            await asyncio.wait_for(resume.wait(),5)
        return await original(db,account_id)
    monkeypatch.setattr(bank,'held_cash',pause)
    task=asyncio.create_task(summary(store))
    await asyncio.wait_for(ready.wait(),5)
    try:
        async with store() as db:
            (await db.get(AccountORM,'A1')).balance_fen+=10_000
            (await db.get(WealthLotORM,'fixture-lot')).units_scaled-=1_000_000
            await db.commit()
    finally:
        resume.set()
    old=await task
    assert old['cash_balance_fen']==800_000 and old['positions_market_value_fen']==150_000
    new=await summary(store)
    assert new['cash_balance_fen']==810_000 and new['positions_market_value_fen']==140_000
    assert old['total_assets_fen']==new['total_assets_fen']==1_000_000
    assert old['snapshot_id']!=new['snapshot_id']


def test_versioned_fixtures_and_contract_reject_false_complete_or_fractional_money():
    cases=json.loads((Path(__file__).parents[1]/'fixtures/wealth_portfolio_contract_v1/asset_summary_cases.json').read_text(encoding='utf-8'))
    for dto in cases.values():
        AssetSummary.model_validate(dto)
    assert cases['F18']['total_assets_fen']==1_000_000 and cases['F10']['total_assets_fen'] is None
    for invalid in [{**cases['F18'],'cash_balance_fen':800000.5},{**cases['F18'],'total_assets_fen':1_100_000},
                    {**cases['F10'],'total_assets_fen':850_000},{**cases['F10'],'positions_market_value_fen':0}]:
        with pytest.raises(ValidationError):
            AssetSummary.model_validate(invalid)
