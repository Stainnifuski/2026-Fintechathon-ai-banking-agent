"""Actual current compact catalogue, both engines, no market/model requests."""
import json
from datetime import datetime,timezone
from pathlib import Path
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store
from backend.app.models import AccountORM,UserORM
from backend.app.models.card import CardORM
from backend.app.models.wealth import WealthProductORM,WealthProductVersionORM,WealthNavORM,WealthOrderORM
from backend.app.models.wealth_research import WealthContextORM,WealthContextSelectionORM
from backend.app.services.bank.wealth_compact_upgrade import upgrade,pool,protected_digest
from backend.app.services.bank import wealth_service as bank

ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.asyncio
async def test_existing_ledger_compact_upgrade_repeat_preserves_teammate_cards(store):
    async with store() as db:
        from backend.app.models.wealth import WealthProfileORM
        db.add(WealthProfileORM(user_id='U1',version=3,data={'existing':'profile'},
            created_at=datetime(2020,1,1,tzinfo=timezone.utc),updated_at=datetime(2020,1,1,tzinfo=timezone.utc)))
        db.add(CardORM(card_id='TEAM-CARD',user_id='U1',account_id='A1',card_type='DEBIT',card_number='masked',
            status='LOCKED',lock_reason='teammate setting',single_txn_limit_fen=100,daily_txn_limit_fen=200,
            single_txn_cap_fen=300,daily_txn_cap_fen=400,state_version=7,online_enabled=False))
        db.add(WealthProductORM(product_id='000015',current_version=3,status='on_sale'));await db.flush()
        db.add(WealthProductVersionORM(version_id='000015:3',product_id='000015',version=3,name='existing v3',
            kind='open_fund',category='bond',risk_level=2,rules={'dataset_version':'existing-bank-v3'},
            disclosure={'version':3,'fees':'historical'},created_at=datetime.now(timezone.utc)))
        db.add(WealthProductORM(product_id='999999',current_version=1,status='on_sale'));await db.flush()
        db.add(WealthProductVersionORM(version_id='999999:1',product_id='999999',version=1,name='obsolete unheld material',kind='open_fund',category='bond',risk_level=2,rules={},disclosure={},created_at=datetime.now(timezone.utc)))
        await db.commit();before=await protected_digest(db)
        result=await upgrade(db);await db.commit()
        assert result['real_funds']==pool()['count'] and result['removed_products']==1
        assert await db.get(WealthProductORM,'999999') is None
        assert (await db.get(WealthProductVersionORM,'000015:3')).rules=={'dataset_version':'existing-bank-v3'}
        assert (await db.get(WealthProductORM,'000015')).current_version==pool()['product_version']
        assert (await db.get(CardORM,'TEAM-CARD')).state_version==7
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
        assert (await db.get(UserORM,'U1')).name=='U1'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        ctx=await db.get(WealthContextSelectionORM,'U1');assert ctx
        scope=await db.get(WealthContextORM,ctx.context_id)
        assert scope.account_id=='A1' and scope.data['account_scope']=='shared_bank'
        codes={r['product_id'] for r in await bank.products(db,'U1',facts_only=True)}
        assert set(pool()['codes'])<=codes and not any(c=='999999' for c in codes)
        after=await protected_digest(db)
        for table in before:
            assert all(after[table].get(k)==v for k,v in before[table].items()),table
        repeated=await upgrade(db);await db.commit()
        assert repeated['bound_existing_accounts']==0 and repeated['removed_products']==0
        assert await protected_digest(db)==after

@pytest.mark.asyncio
async def test_out_of_pool_financial_reference_refuses_without_deleting(store):
    # A historical settled order is just as protected as an active lot.
    async with store() as db:
        from backend.app.models.wealth import WealthLotORM
        from datetime import datetime,timezone
        db.add(WealthProductORM(product_id='999999',current_version=1,status='on_sale'));await db.flush()
        db.add(WealthProductVersionORM(version_id='999999:1',product_id='999999',version=1,name='held',kind='open_fund',category='bond',risk_level=2,rules={},disclosure={},created_at=datetime.now(timezone.utc)))
        await db.flush()
        now=datetime.now(timezone.utc)
        db.add(WealthOrderORM(operation_id='historic',user_id='U1',account_id='A1',version_id='999999:1',
            direction='purchase',amount_fen=100,units_scaled=10000,param_digest='historical',authorization_id='historical',
            plan_version=1,status='SETTLED',accepted_at=now,pricing_date=now.date(),confirm_date=now.date(),cash_date=now.date(),details={'source':'isolated historical record'}))
        await db.flush()
        db.add(WealthLotORM(lot_id='protected-lot',operation_id='historic',user_id='U1',account_id='A1',version_id='999999:1',units_scaled=10000,cost_fen=100,
            acquired_date=now.date(),unlock_date=now.date()))
        await db.commit()
        with pytest.raises(ValueError,match='999999'):await upgrade(db)
        await db.rollback()
        assert await db.get(WealthProductORM,'999999') is not None
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000


@pytest.mark.asyncio
async def test_newer_teammate_product_version_is_not_downgraded(store):
    async with store() as db:
        newer=pool()['product_version']+1
        db.add(WealthProductORM(product_id='000015',current_version=newer,status='on_sale'))
        await db.commit()
        with pytest.raises(ValueError,match='禁止降级'):await upgrade(db)
        await db.rollback()
        assert (await db.get(WealthProductORM,'000015')).current_version==newer
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
