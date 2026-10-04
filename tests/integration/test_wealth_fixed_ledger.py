"""B01/B02/C01/C02: actual fixed prices through bank transactions in both isolated stores."""
from datetime import datetime
from decimal import Decimal,ROUND_HALF_UP
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,assess,authorize
from backend.app.domain.wealth_fixed_data import bank_products
from backend.app.models import AccountORM,TransactionORM
from backend.app.models.wealth import WealthSettlementORM,WealthQuickQuotaORM
from backend.app.models.wealth_research import WealthContextORM
from backend.app.schemas.wealth import InvestmentCommand
from backend.app.services.bank import wealth_service as bank,wealth_research as research
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
@pytest.mark.parametrize('pid',['B017','B018'])
async def test_fixed_prices_standard_and_quick_cash_reconcile(store,pid):
    async with store() as db:
        products=[p for p in bank_products() if p['product_id']==pid]
        for p in products:p['rules'].update(dataset_id='fixed-ledger-test',dataset_version='20261003-fixed')
        payload={'schema_version':1,'dataset_id':'fixed-ledger-test','dataset_version':'20261003-fixed',
            'mode':'snapshot','products':products}
        imported=await research.import_dataset(db,payload)
        context=await research.initialize(db,'U1',dataset_key=imported['dataset_key'])
        ctx=await db.get(WealthContextORM,context['context_id'])
        async def clock(value):
            ctx.data={**ctx.data,'business_at':value};await db.commit()
        await clock('2026-09-29T14:00:00+08:00')
        await research.select_context(db,'U1',ctx.context_id,1);await assess(db)
        buy=InvestmentCommand(operation_id='fixed-buy-'+pid,direction='purchase',account_id=ctx.account_id,
            product_id=pid,product_version=1,disclosure_version=1,amount_fen=100000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        await clock('2026-09-30T19:00:00+08:00')
        settled=await bank.settle(db,buy.operation_id)
        assert settled['status']=='SETTLED',settled
        standard=buy.model_copy(update={'operation_id':'fixed-standard-'+pid,'direction':'redeem','amount_fen':0,'units':'50.0000'})
        view=await bank.precheck(db,'U1',standard)
        assert view['pricing_date']=='2026-10-08' and view['cash_date']=='2026-10-09'
        await bank.accept_many(db,'U1',[(standard,await authorize(db,standard))])
        quick=standard.model_copy(update={'operation_id':'fixed-quick-'+pid,'units':'10.0000','command_version':2,'redemption_mode':'quick'})
        if pid=='B018':
            await clock('2026-10-04T10:00:00+08:00')
            with pytest.raises(BankError):await bank.precheck(db,'U1',quick)
            await db.rollback();ctx=await db.get(WealthContextORM,context['context_id'])
            await clock('2026-10-08T10:00:00+08:00')
        else:await clock('2026-10-04T10:00:00+08:00')
        preview=await bank.precheck(db,'U1',quick)
        assert preview['estimated_nav_date']=='2026-09-30'
        quick=InvestmentCommand.model_validate(preview['command'])
        aid=await authorize(db,quick)
        with pytest.raises(BankError):await bank.accept_many(db,'U1',[(quick.model_copy(update={'units':'11.0000'}),aid)])
        await db.rollback()
        before=(await db.get(AccountORM,quick.account_id)).balance_fen
        paid=(await bank.accept_many(db,'U1',[(quick,aid)]))[0]
        assert paid['status']=='SETTLED'
        assert (await db.get(AccountORM,quick.account_id)).balance_fen==before+paid['details']['actual']['cash_fen']
        assert (await bank.accept_many(db,'U1',[(quick,aid)]))[0]['status']=='SETTLED'
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.operation_id==quick.operation_id))==1
        quota=await db.scalar(select(WealthQuickQuotaORM));assert quota.paid_fen==paid['details']['actual']['cash_fen']
        with pytest.raises(BankError):await bank.cancel(db,'U1',quick.operation_id)
        await db.rollback();ctx=await db.get(WealthContextORM,context['context_id'])
        await clock('2026-10-09T19:00:00+08:00')
        before=(await db.get(AccountORM,quick.account_id)).balance_fen
        final=await bank.settle(db,standard.operation_id)
        assert final['status']=='SETTLED'
        amount=final['details']['actual']['cash_fen']
        assert (await db.get(AccountORM,quick.account_id)).balance_fen==before+amount
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.operation_id==standard.operation_id))==1
        assert (await bank.settle(db,standard.operation_id))['details']==final['details']
        assert (await db.get(AccountORM,quick.account_id)).balance_fen==before+amount
