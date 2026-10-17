"""Shared-account integration; never touches a running business database."""
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from backend.app.models import AccountORM
from backend.app.models.wealth_research import WealthContextORM, WealthDatasetORM, WealthContextSelectionORM
from backend.app.models.wealth import WealthCashHoldORM
from backend.app.services.bank import wealth_research as research, wealth_service as bank
from backend.app.services.bank.data_service import list_accounts
from backend.app.services.bank.transfer_service import require_ordinary_accounts, BankError
from tests.integration.test_wealth_bank import store

async def seed_legacy_purchase_hold(db, operation_id, amount_fen):
    """Create a real accepted legacy purchase before binding the shared context.

    Both databases require the hold's referenced order; a detached hold is not
    a valid bank state even if SQLite foreign-key enforcement is disabled.
    """
    from tests.integration.test_wealth_bank import assess, authorize, command
    await assess(db)
    cmd = command(operation_id, amount=amount_fen)
    authorization_id = await authorize(db, cmd)
    result = (await bank.accept_many(db, 'U1', [(cmd, authorization_id)]))[0]
    assert result['status'] == 'ACCEPTED'
    hold = await db.get(WealthCashHoldORM, operation_id)
    assert hold.account_id == 'A1' and hold.amount_fen == amount_fen and hold.status == 'HELD'

async def seed_dataset(db):
    db.add(WealthDatasetORM(dataset_key='shared-test:v1',dataset_id='shared-test',version='v1',
        mode='snapshot',manifest={'policy_version':'test-only'},ingested_at=datetime.now(timezone.utc)))
    await db.flush()

@pytest.mark.asyncio
async def test_shared_binding_preserves_money_and_cross_scene_holds(store):
    async with store() as db:
        await seed_legacy_purchase_hold(db, 'test-pending', 12345)
        await seed_dataset(db)
        before=await db.scalar(select(func.count()).select_from(AccountORM))
        result=await research.bind_shared_account(db,'U1','A1','shared-test:v1')
        assert result['cash_issued_fen']==0
        assert await db.scalar(select(func.count()).select_from(AccountORM))==before
        db.add(WealthContextSelectionORM(user_id='U1',context_id=result['context_id'],version=1))
        # Another ordinary account belongs to the same bank asset scope.
        db.add(AccountORM(account_id='A3',user_id='U1',account_type='savings',card_number='test3',balance_fen=10000))
        await db.commit()
        rows=await list_accounts('U1',session=db)
        account=next(a for a in rows if a['account_id']=='A1')
        assert account['held_fen']==12345 and account['available_fen']==2000000-12345
        await require_ordinary_accounts(db,['A1'])
        assets=(await bank.assets(db,'U1'))['asset_summary']
        assert assets['account_ids']==['A1','A3']
        assert assets['cash_balance_fen']==2010000
        assert assets['held_fen']==12345 and assets['available_cash_fen']==2010000-12345
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
        again=await research.bind_shared_account(db,'U1','A1','shared-test:v1')
        assert not again['created']
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000

@pytest.mark.asyncio
async def test_sandbox_stays_isolated_and_ownership_is_required(store,monkeypatch):
    async with store() as db:
        await seed_dataset(db)
        db.add(WealthContextORM(context_id='old-sandbox',user_id='U1',account_id='A1',
            dataset_key='shared-test:v1',version=1,data={'disposable':True}))
        await db.commit()
        assert await list_accounts('U1',session=db)==[]
        with pytest.raises(BankError): await require_ordinary_accounts(db,['A1'])
        with pytest.raises(BankError): await research.bind_shared_account(db,'U2','A1','shared-test:v1')
        await db.rollback()
        await research.bind_shared_account(db,'U1','A1','shared-test:v1')
        from backend.app.services.bank import wealth_demo
        monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED','true')
        with pytest.raises(BankError): await wealth_demo.context_owned(db,'U1','old-sandbox')
        from tests.integration.test_wealth_bank import REAL_BUSINESS_NOW
        at=await REAL_BUSINESS_NOW(db,'A1')
        assert abs((datetime.now(timezone.utc)-at).total_seconds())<5


@pytest.mark.asyncio
async def test_transfer_consumes_the_same_cash_and_respects_wealth_hold(store):
    from dataclasses import replace
    from backend.app.services.bank.transfer_service import BankTransferCommand, TrustedExecutionContext, _digest, submit_transfer
    from backend.app.models import TransactionORM
    from backend.app.services.bank.bill_service import scope
    from backend.app.schemas.bills import BillQuery
    async with store() as db:
        await seed_legacy_purchase_hold(db, 'test-held', 1900000)
        await seed_dataset(db)
        linked=await research.bind_shared_account(db,'U1','A1','shared-test:v1')
        db.add(WealthContextSelectionORM(user_id='U1',context_id=linked['context_id'],version=1))
        await db.commit()
        cmd=BankTransferCommand('blocked','A1','A2',200000,'CNY','')
        cmd=replace(cmd,param_digest=_digest(cmd))
        with pytest.raises(BankError,match='余额不足'):
            await submit_transfer(db,cmd,TrustedExecutionContext('U1','test-auth',1))
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
        cmd=BankTransferCommand('allowed','A1','A2',50000,'CNY','')
        cmd=replace(cmd,param_digest=_digest(cmd))
        await submit_transfer(db,cmd,TrustedExecutionContext('U1','test-auth',1))
        assets=(await bank.assets(db,'U1'))['asset_summary']
        rows=await list_accounts('U1',session=db)
        assert rows[0]['account_id']=='A1'
        assert rows[0]['balance']==19500
        assert rows[0]['available_fen']==assets['available_cash_fen']==50000
        assert assets['cash_balance_fen']==1950000
        predicates=await scope(db,'U1',BillQuery())
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(*predicates))==1


@pytest.mark.asyncio
async def test_shared_account_purchase_uses_existing_bank_cash(store):
    from backend.app.models.wealth import WealthProductVersionORM
    from tests.integration.test_wealth_bank import assess, authorize, command
    async with store() as db:
        await seed_dataset(db)
        bound=await research.bind_shared_account(db,'U1','A1','shared-test:v1')
        db.add(WealthContextSelectionORM(user_id='U1',context_id=bound['context_id'],version=1))
        product=await db.get(WealthProductVersionORM,'MF002:1')
        product.rules={**product.rules,'dataset_id':'shared-test','dataset_version':'v1'}
        await db.commit()
        await assess(db)
        cmd=command('shared-bank-purchase',amount=100000)
        from backend.app.schemas.wealth import InvestmentCommand
        cmd=InvestmentCommand.model_validate((await bank.precheck(db,'U1',cmd))['command'])
        aid=await authorize(db,cmd)
        await bank.precheck(db,'U1',cmd)
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
        await bank.accept_many(db,'U1',[(cmd,aid)])
        rows=await list_accounts('U1',session=db)
        summary=(await bank.assets(db,'U1'))['asset_summary']
        assert rows[0]['account_id']==summary['account_ids'][0]=='A1'
        assert rows[0]['held_fen']==summary['held_fen']==0
        assert rows[0]['available_fen']==summary['available_cash_fen']>=1900000
        assert rows[0]['balance']<20000
        assert summary['positions_market_value_fen']>0
