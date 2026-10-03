"""两只快赎合同/绑定与立即到账：独立双库金融关系核对。"""
from datetime import datetime
from decimal import Decimal
import hashlib, json

import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, assess, authorize
from backend.app.schemas.wealth import InvestmentCommand
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth import WealthSettlementORM, WealthLotORM, WealthQuickQuotaORM
from backend.app.models.wealth_research import WealthContextORM
from backend.app.services.bank import wealth_service as bank, wealth_research as research
from backend.app.services.bank.wealth_preload import bank_products
from backend.app.services.bank.wealth_quick import window_open
from backend.app.services.bank.transfer_service import BankError
from backend.app.domain.wealth_command import command_digest
import asyncio


def test_legacy_digest_and_fixed_quick_windows():
    cmd = InvestmentCommand(operation_id="legacy-digest-01",direction="purchase",product_id="MF001",product_version=1,disclosure_version=1,account_id="A1",amount_fen=10000)
    old = cmd.model_dump(exclude={"operation_id","command_version","redemption_mode","nav_quote_id","quoted_cash_fen","context_id","context_epoch"})
    assert command_digest(cmd) == hashlib.sha256(json.dumps(old,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
    products = {p['product_id']:p for p in bank_products()}
    one = products['B017']['rules']['quick_redemption']; two=products['B018']['rules']['quick_redemption']
    for stamp, expected in [('2026-10-03T00:04:59+08:00',False),('2026-10-03T00:05:00+08:00',True),('2026-10-03T23:55:00+08:00',False)]:
        assert window_open(one,datetime.fromisoformat(stamp),'synthetic-weekdays-v1') == expected
    assert window_open(two,datetime.fromisoformat('2026-10-02T09:00:00+08:00'),'synthetic-weekdays-v1')
    assert not window_open(two,datetime.fromisoformat('2026-10-02T17:00:00+08:00'),'synthetic-weekdays-v1')
    assert not window_open(two,datetime.fromisoformat('2026-10-03T10:00:00+08:00'),'synthetic-weekdays-v1')


@pytest.mark.asyncio
@pytest.mark.parametrize('pid',['B017','B018'])
async def test_quick_cash_atomic_unique_quota_and_quote_binding(store,pid):
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'quick-test','mode':'snapshot','products':bank_products()}
        for p in payload['products']: p['rules']['dataset_version']='quick-test'
        assert (await research.import_dataset(db,payload))['ok']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:quick-test')
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-30T19:00:00+08:00'}
        await db.commit(); await research.select_context(db,'U1',ctx.context_id,1)
        await assess(db)
        buy=InvestmentCommand(operation_id='quick-buy-'+pid,direction='purchase',product_id=pid,product_version=1,disclosure_version=1,account_id=context['account_id'],amount_fen=2000000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        ctx=await db.get(WealthContextORM,context['context_id']); ctx.data={**ctx.data,'business_at':'2026-10-02T10:00:00+08:00'}; await db.commit()
        assert (await bank.settle(db,buy.operation_id))['status']=='SETTLED'
        initial=(await db.get(AccountORM,buy.account_id)).balance_fen
        lot=(await db.scalars(select(WealthLotORM).where(WealthLotORM.account_id==buy.account_id))).one()
        original_units,original_cost=lot.units_scaled,lot.cost_fen
        unbound=InvestmentCommand(operation_id='quick-redeem-'+pid,direction='redeem',product_id=pid,product_version=1,disclosure_version=1,account_id=buy.account_id,units='1000.0000',command_version=2,redemption_mode='quick')
        preview=await bank.precheck(db,'U1',unbound)
        bound=InvestmentCommand.model_validate(preview['command'])
        assert preview['cash_date']==preview['pricing_date']
        assert bound.quoted_cash_fen and bound.context_id and bound.nav_quote_id
        tampered=bound.model_copy(update={'quoted_cash_fen':bound.quoted_cash_fen+1})
        with pytest.raises(BankError,match='重新预览'): await bank.precheck(db,'U1',tampered)
        aid=await authorize(db,bound)
        done=(await bank.accept_many(db,'U1',[(bound,aid)]))[0]
        assert done['status']=='SETTLED'
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==initial+bound.quoted_cash_fen
        await db.refresh(lot)
        assert lot.units_scaled==original_units-10000000 and 0<lot.cost_fen<original_cost
        again=(await bank.accept_many(db,'U1',[(bound,aid)]))[0]
        assert again['status']=='SETTLED'
        await bank.settle(db,bound.operation_id)
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==initial+bound.quoted_cash_fen
        assert await db.scalar(select(func.count()).select_from(WealthSettlementORM).where(WealthSettlementORM.operation_id==bound.operation_id))==1
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.tx_id==bank.stable_id('WRT',bound.operation_id)))==1
        assert await db.scalar(select(TransactionORM.operation_id).where(TransactionORM.tx_id==bank.stable_id('WRT',bound.operation_id)))==bound.operation_id
        quota=(await db.scalars(select(WealthQuickQuotaORM))).one(); assert quota.paid_fen==bound.quoted_cash_fen
        assert (await bank.cancellation_eligibility(db,'U1',bound.operation_id))['eligibility']=='DENIED'
        with pytest.raises(BankError): await bank.cancel(db,'U1',bound.operation_id)


@pytest.mark.asyncio
async def test_quick_multi_account_concurrency_shared_quota_and_transaction_fault(store,monkeypatch):
    from backend.app.services.bank import wealth_quick
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'quick-race','mode':'snapshot','products':bank_products()}
        for product in payload['products']:product['rules']['dataset_version']='quick-race'
        assert (await research.import_dataset(db,payload))['ok']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:quick-race')
        first=await db.get(WealthContextORM,context['context_id'])
        first.data={**first.data,'run_id':'RUNquick-race','business_at':'2026-09-30T19:00:00+08:00'}
        db.add(AccountORM(account_id='SAquick-peer',user_id='U1',account_type='savings',card_number='fictional-peer',balance_fen=2000000,daily_transfer_limit_fen=5000000))
        await db.flush()
        db.add(WealthContextORM(context_id='CTXquick-peer',user_id='U1',account_id='SAquick-peer',dataset_key=first.dataset_key,
            version=1,data={**first.data}))
        await db.commit();await research.select_context(db,'U1',first.context_id,1);await assess(db)
        account_ids=[first.account_id,'SAquick-peer']; ctx_ids=[first.context_id,'CTXquick-peer']
        for i,aid in enumerate(account_ids):
            buy=InvestmentCommand(operation_id='multi-quick-buy-'+str(i),direction='purchase',product_id='B017',product_version=1,disclosure_version=1,account_id=aid,amount_fen=2000000)
            await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        for cid in ctx_ids:
            ctx=await db.get(WealthContextORM,cid);ctx.data={**ctx.data,'business_at':'2026-10-02T10:00:00+08:00'}
        await db.commit()
        for i in range(2):assert (await bank.settle(db,'multi-quick-buy-'+str(i)))['status']=='SETTLED'
        commands=[]
        for i,aid in enumerate(account_ids):
            unbound=InvestmentCommand(operation_id='multi-quick-sell-'+str(i),direction='redeem',product_id='B017',product_version=1,disclosure_version=1,account_id=aid,units='6000.0000',command_version=2,redemption_mode='quick')
            cmd=InvestmentCommand.model_validate((await bank.precheck(db,'U1',unbound))['command'])
            commands.append((cmd,await authorize(db,cmd)))
        # 失败点位于份额/现金/额度变更后：显式事务回滚恢复全部事实和授权。
        before=[(await db.get(AccountORM,aid)).balance_fen for aid in account_ids]
        original=wealth_quick.record_bank_event
        def fail_event(*args,**kwargs):raise RuntimeError('controlled-transaction-fault')
        monkeypatch.setattr(wealth_quick,'record_bank_event',fail_event)
        with pytest.raises(RuntimeError):await bank.accept_many(db,'U1',[commands[0]])
        await db.rollback();monkeypatch.setattr(wealth_quick,'record_bank_event',original)
        assert [(await db.get(AccountORM,aid)).balance_fen for aid in account_ids]==before
        assert await db.scalar(select(func.count()).select_from(WealthQuickQuotaORM))==0
    async def submit(pair):
        async with store() as db:
            try:return (await bank.accept_many(db,'U1',[pair]))[0]['status']
            except BankError as error:
                await db.rollback();return error.code
    results=await asyncio.gather(*(submit(pair) for pair in commands))
    assert sorted(results)==['QUICK_DAILY_LIMIT','SETTLED']
    async with store() as db:
        quota=(await db.scalars(select(WealthQuickQuotaORM))).one()
        assert quota.scope_id=='RUNquick-race' and quota.paid_fen<=1000000
        assert await db.scalar(select(func.count()).select_from(WealthSettlementORM).where(WealthSettlementORM.kind=='quick_cash'))==1
        after=[(await db.get(AccountORM,aid)).balance_fen for aid in account_ids]
        assert sum(after)-sum(before)==quota.paid_fen
        assert len((await bank.assets(db,'U1'))['cash'])==2
