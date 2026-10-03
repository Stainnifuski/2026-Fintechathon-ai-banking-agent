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
        quota=(await db.scalars(select(WealthQuickQuotaORM))).one(); assert quota.paid_fen==bound.quoted_cash_fen
        assert (await bank.cancellation_eligibility(db,'U1',bound.operation_id))['eligibility']=='DENIED'
        with pytest.raises(BankError): await bank.cancel(db,'U1',bound.operation_id)
