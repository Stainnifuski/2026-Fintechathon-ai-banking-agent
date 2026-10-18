from datetime import datetime,timezone
import pytest
from sqlalchemy import select,func
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthOrderORM,WealthCashHoldORM
from backend.app.services.bank.wealth_retire_pending import retire
from tests.integration.test_wealth_bank import store


@pytest.mark.asyncio
async def test_retire_unpriced_purchase_releases_once_without_crediting_principal(store):
    async with store() as db:
        now=datetime.now(timezone.utc)
        db.add(WealthOrderORM(operation_id='legacy-held',user_id='U1',account_id='A1',version_id='MF002:1',
            direction='purchase',amount_fen=1440000,units_scaled=0,param_digest='legacy',authorization_id='legacy',
            plan_version=1,status='PENDING_PRICING',accepted_at=now,pricing_date=now.date(),confirm_date=now.date(),cash_date=now.date(),details={}))
        await db.flush()
        db.add(WealthCashHoldORM(operation_id='legacy-held',account_id='A1',amount_fen=1440000,status='HELD'))
        await db.commit()
        before=(await db.get(AccountORM,'A1')).balance_fen
        result=await retire(db);await db.commit()
        assert result['cancelled']==1 and result['released_fen']==1440000
        assert (await db.get(AccountORM,'A1')).balance_fen==before
        assert (await db.get(WealthCashHoldORM,'legacy-held')).status=='RELEASED'
        assert (await db.get(WealthOrderORM,'legacy-held')).status=='CANCELLED'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.status.in_(['ACCEPTED','PENDING_PRICING','PENDING_CASH'])))==0
        again=await retire(db);await db.commit()
        assert again=={'cancelled':0,'released_fen':0,'receivables_paid_fen':0}
        assert (await db.get(AccountORM,'A1')).balance_fen==before


@pytest.mark.asyncio
async def test_retire_priced_redemption_pays_recorded_receivable_once(store):
    from backend.app.models.wealth import WealthSettlementORM
    async with store() as db:
        now=datetime.now(timezone.utc)
        db.add(WealthOrderORM(operation_id='legacy-receivable',user_id='U1',account_id='A1',version_id='MF002:1',
            direction='redeem',amount_fen=0,units_scaled=10000,param_digest='legacy',authorization_id='legacy',
            plan_version=1,status='PENDING_CASH',accepted_at=now,pricing_date=now.date(),confirm_date=now.date(),cash_date=now.date(),details={'actual':{'cash_fen':12345}}))
        await db.commit();before=(await db.get(AccountORM,'A1')).balance_fen
        result=await retire(db);await db.commit()
        assert result['receivables_paid_fen']==12345
        assert (await db.get(AccountORM,'A1')).balance_fen==before+12345
        assert (await db.get(WealthOrderORM,'legacy-receivable')).status=='SETTLED'
        assert await db.get(WealthSettlementORM,'legacy-receivable:cash')
        assert (await retire(db))['receivables_paid_fen']==0
        await db.commit()
        assert (await db.get(AccountORM,'A1')).balance_fen==before+12345
