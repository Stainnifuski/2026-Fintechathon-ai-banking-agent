"""New confirmed shared-bank trades settle instantly; old fills are immutable."""
from datetime import timedelta
from sqlalchemy import select, func
import pytest
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth import WealthProductVersionORM, WealthLotORM, WealthOrderORM, WealthCashHoldORM, WealthNavORM
from backend.app.models.wealth_research import WealthContextSelectionORM
from backend.app.schemas.wealth import InvestmentCommand
from backend.app.services.bank import wealth_service as bank, wealth_research as research
from backend.app.services.bank.transfer_service import BankError
from tests.integration.test_wealth_bank import store, http_store, assess, authorize, command, NOW
from tests.integration.test_wealth_shared_accounts import seed_dataset


async def setup(db, monkeypatch, product='MF002'):
    await seed_dataset(db)
    linked=await research.bind_shared_account(db,'U1','A1','shared-test:v1')
    db.add(WealthContextSelectionORM(user_id='U1',context_id=linked['context_id'],version=1))
    version=await db.get(WealthProductVersionORM,product+':1')
    version.rules={**version.rules,'dataset_id':'shared-test','dataset_version':'v1'}
    await db.commit()
    await assess(db)
    async def clock(*args, **kwargs): return NOW
    monkeypatch.setattr(bank,'business_now',clock)
    return version


async def bound(db, cmd):
    view=await bank.precheck(db,'U1',cmd)
    assert view['execution_policy']=='competition-instant-v1'
    return InvestmentCommand.model_validate(view['command'])


@pytest.mark.asyncio
async def test_instant_purchase_redemption_and_replay(store,monkeypatch):
    async with store() as db:
        await setup(db,monkeypatch)
        cmd=await bound(db,command('instant-buy'))
        aid=await authorize(db,cmd)
        result=(await bank.accept_many(db,'U1',[(cmd,aid)]))[0]
        assert result['status']=='SETTLED'
        balance=(await db.get(AccountORM,'A1')).balance_fen
        assert balance==2000000-result['details']['actual']['gross_fen']+result['details']['actual']['refund_fen']
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        lot=await db.scalar(select(WealthLotORM))
        assert lot.units_scaled>0 and lot.acquired_date==NOW.date()
        assert (await bank.accept_many(db,'U1',[(cmd,aid)]))[0]==result
        assert (await db.get(AccountORM,'A1')).balance_fen==balance
        sell=await bound(db,command('instant-sell',direction='redeem',amount=0,quantity='10.0000'))
        sid=await authorize(db,sell)
        old_units,old_cost=lot.units_scaled,lot.cost_fen
        redeemed=(await bank.accept_many(db,'U1',[(sell,sid)]))[0]
        actual=redeemed['details']['actual']
        assert redeemed['status']=='SETTLED' and actual['fee_fen']>0
        assert lot.units_scaled==old_units-100000 and lot.cost_fen==old_cost-actual['cost_fen']
        assert (await db.get(AccountORM,'A1')).balance_fen==balance+actual['cash_fen']
        assert await db.scalar(select(func.count()).select_from(TransactionORM))==2
        assert await bank.settle(db,sell.operation_id)==redeemed


@pytest.mark.asyncio
async def test_quote_change_old_approval_and_future_nav(store,monkeypatch):
    async with store() as db:
        v=await setup(db,monkeypatch)
        old=command('old-preview')
        aid=await authorize(db,old)
        with pytest.raises(BankError,match='重新预览'):
            await bank.accept_many(db,'U1',[(old,aid)])
        await db.rollback()
        cmd=await bound(db,command('quote-change'))
        aid=await authorize(db,cmd)
        price=await bank.latest_nav(db,v.version_id,NOW)
        price.price='1.12345678'
        price_id=price.nav_id
        await db.commit()
        with pytest.raises(BankError,match='报价'):
            await bank.accept_many(db,'U1',[(cmd,aid)])
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        # Future published data cannot enter a new quote.
        price=await db.get(WealthNavORM,price_id)
        price.available_at=NOW+timedelta(days=1)
        await db.commit()
        view=await bank.precheck(db,'U1',command('visible-only'))
        assert view['estimated_nav_date']<price.nav_date.isoformat()


@pytest.mark.asyncio
async def test_holding_and_atomic_batch_failure(store,monkeypatch):
    async with store() as db:
        await setup(db,monkeypatch,'MW002')
        buy=await bound(db,command('locked-buy',product='MW002'))
        aid=await authorize(db,buy)
        await bank.accept_many(db,'U1',[(buy,aid)])
        with pytest.raises(BankError,match='持有期'):
            await bound(db,command('too-soon',product='MW002',direction='redeem',amount=0,quantity='1.0000'))
        await db.rollback()
        one=await bound(db,command('batch-one',product='MW002',amount=100000))
        two=await bound(db,command('batch-two',product='MW002',amount=1900000))
        aid1,aid2=await authorize(db,one),await authorize(db,two)
        cash=(await db.get(AccountORM,'A1')).balance_fen
        with pytest.raises(BankError):
            await bank.accept_many(db,'U1',[(one,aid1),(two,aid2)])
        await db.rollback()
        assert (await db.get(AccountORM,'A1')).balance_fen==cash
        assert await db.get(WealthOrderORM,'batch-one') is None


@pytest.mark.asyncio
async def test_group_preview_binds_quote_and_mfa_settles_all(store,http_store,monkeypatch):
    import pyotp
    from backend.app.models import UserORM
    from backend.app.models.wealth import WealthPlanORM, WealthProfileORM
    client, local, _=http_store
    secret=pyotp.random_base32()
    async with store() as db:
        await setup(db,monkeypatch)
        user=await db.get(UserORM,'U1')
        user.totp_secret,user.totp_enabled=secret,True
        version=(await db.get(WealthProfileORM,'U1')).version
        items=[{'command':command('instant-group-'+str(i),amount=100000).model_copy(update={'plan_id':'instant-plan','plan_version':1}).model_dump(),
            'status':'AWAITING_CONFIRMATION','reason':'isolated test'} for i in range(2)]
        db.add(WealthPlanORM(plan_id='instant-plan',user_id='U1',version=1,status='AWAITING_CONFIRMATION',
            data={'items':items,'stage':'BUY','profile_version':version,'goal_versions':{},'request':{},'configuration':{}}))
        await db.commit()
    base='/api/v1/wealth/plans/instant-plan'
    response=await client.post(base+'/preview',json={'version':1})
    assert response.status_code==200,response.text
    views=response.json()['items']
    assert all(v['command']['command_version']==2 and v['execution_policy']=='competition-instant-v1' for v in views)
    confirmed=await client.post(base+'/confirm',json={'version':1,'items':[{'authorization_id':v['authorization_id'],
        'digest':v['digest'],'disclosure_version':1,'accepted':True} for v in views]})
    assert confirmed.status_code==200,confirmed.text
    verified=await client.post(base+'/verify',json={'version':1,'proof':pyotp.TOTP(secret).now(),'method':'totp'})
    assert verified.status_code==200,verified.text
    executed=await client.post(base+'/execute',json={'version':1})
    assert executed.status_code==200,executed.text
    assert executed.json()['status']=='COMPLETED'
    assert all(item['status']=='SETTLED' for item in executed.json()['items'])
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthLotORM))==2
        assert await db.scalar(select(func.count()).select_from(TransactionORM))==2
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0


@pytest.mark.asyncio
async def test_r2_cannot_buy_r4_under_instant_policy(store,monkeypatch):
    from backend.app.models.wealth import WealthAssessmentORM
    from backend.app.integrations.wealth_authorization import WealthPolicyError
    async with store() as db:
        await setup(db,monkeypatch,'MF004')
        assessment=await db.scalar(select(WealthAssessmentORM))
        assessment.result={**assessment.result,'capacity_level':2}
        await db.commit()
        with pytest.raises(WealthPolicyError) as error:
            await bank.accept_many(db,'U1',[(command('r2-r4-instant',product='MF004'), 'no-bypass')])
        assert error.value.code=='SUITABILITY_MISMATCH'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000


@pytest.mark.asyncio
async def test_cash_management_cannot_bypass_quick_window_or_quota(store,monkeypatch):
    async with store() as db:
        v=await setup(db,monkeypatch)
        contract={'window':'natural_day','start':'00:00','end':'23:59','daily_limit_fen':10000}
        v.rules={**v.rules,'quick_redemption':contract}
        await db.commit()
        cmd=await bound(db,command('cash-buy'))
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        ordinary=command('cash-sell',direction='redeem',amount=0,quantity='10.0000')
        sell=await bound(db,ordinary)
        assert sell.redemption_mode=='quick' and sell.nav_quote_id
        done=(await bank.accept_many(db,'U1',[(sell,await authorize(db,sell))]))[0]
        assert done['status']=='SETTLED'
        with pytest.raises(BankError) as error:
            await bound(db,ordinary.model_copy(update={'operation_id':'cash-quota','units':'100.0000'}))
        assert error.value.code=='QUICK_DAILY_LIMIT'
        v.rules={**v.rules,'quick_redemption':{**contract,'start':'15:00','end':'16:00'}}
        await db.commit()
        with pytest.raises(BankError) as error:
            await bound(db,ordinary.model_copy(update={'operation_id':'cash-window'}))
        assert error.value.code=='QUICK_WINDOW_CLOSED'
