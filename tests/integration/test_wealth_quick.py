"""两只快赎合同/绑定与立即到账：独立双库金融关系核对。"""
from datetime import datetime
from decimal import Decimal
import hashlib, json

import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, http_store, assess, authorize
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


@pytest.mark.asyncio
@pytest.mark.parametrize('pid',['B017','B018'])
async def test_new_cash_products_keep_standard_redemption_settlement(store,pid):
    """新产品普通赎回仍占份额、T+1到账，不偷换成立即快赎。"""
    from datetime import date, timezone
    from backend.app.models.wealth import WealthNavORM, WealthUnitHoldORM
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'standard-compat',
                 'mode':'snapshot','products':bank_products()}
        for product in payload['products']:product['rules']['dataset_version']='standard-compat'
        assert (await research.import_dataset(db,payload))['ok']
        assert (await research.import_dataset(db,payload))['duplicate']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:standard-compat')
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-29T14:00:00+08:00'}
        await db.commit();await research.select_context(db,'U1',ctx.context_id,1);await assess(db)
        buy=InvestmentCommand(operation_id='standard-opening-'+pid,direction='purchase',account_id=ctx.account_id,
            product_id=pid,product_version=1,disclosure_version=1,amount_fen=100000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        ctx.data={**ctx.data,'business_at':'2026-09-30T10:00:00+08:00'};await db.commit()
        assert (await bank.settle(db,buy.operation_id))['status']=='SETTLED'
        initial=(await db.get(AccountORM,buy.account_id)).balance_fen
        ctx.data={**ctx.data,'business_at':'2026-09-30T19:00:00+08:00'};await db.commit()
        sell=InvestmentCommand(operation_id='standard-sell-'+pid,direction='redeem',account_id=buy.account_id,
            product_id=pid,product_version=1,disclosure_version=1,units='100.0000')
        preview=await bank.precheck(db,'U1',sell)
        assert preview['cash_date']>preview['pricing_date']
        assert (await bank.accept_many(db,'U1',[(sell,await authorize(db,sell))]))[0]['status']=='ACCEPTED'
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==initial
        assert await db.scalar(select(func.count()).select_from(WealthUnitHoldORM).where(WealthUnitHoldORM.status=='HELD'))==1
        # 隔离测试中的确定性模拟净值；18点发布前不能被当作已知价格。
        price=await db.get(WealthNavORM,pid+':1:2026-10-08')
        if price is None:
            price=WealthNavORM(nav_id=pid+':1:2026-10-08',version_id=pid+':1',nav_date=date(2026,10,8),price='1.00000000')
            db.add(price)
        price.price='1.00000000';price.available_at=datetime(2026,10,8,10,tzinfo=timezone.utc)
        ctx.data={**ctx.data,'business_at':'2026-10-09T10:00:00+08:00'};await db.commit()
        settled=await bank.settle(db,sell.operation_id)
        assert settled['status']=='SETTLED' and settled['details']['actual']['cash_fen']==10000
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==initial+10000
        assert await db.scalar(select(func.count()).select_from(WealthQuickQuotaORM))==0
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.operation_id==sell.operation_id))==1


@pytest.mark.asyncio
async def test_quick_http_lost_response_requires_new_buy_confirmation(store, http_store, monkeypatch):
    """真实银行HTTP路由成交后丢响应：原号恢复且到账后买入不能沿用赎回授权。"""
    monkeypatch.delenv('WEALTH_DEMO_VERIFICATION_ENABLED', raising=False)
    client, local, _ = http_store
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'quick-http',
                 'mode':'snapshot','products':bank_products()}
        for product in payload['products']:product['rules']['dataset_version']='quick-http'
        assert (await research.import_dataset(db,payload))['ok']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:quick-http')
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-29T14:00:00+08:00'}
        await db.commit();await research.select_context(db,'U1',ctx.context_id,1);await assess(db)
        buy=InvestmentCommand(operation_id='http-quick-opening-buy',direction='purchase',account_id=context['account_id'],
            product_id='B017',product_version=1,disclosure_version=1,amount_fen=2000000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-30T10:00:00+08:00'};await db.commit()
        assert (await bank.settle(db,buy.operation_id))['status']=='SETTLED'
        initial=(await db.get(AccountORM,buy.account_id)).balance_fen
    async def preview_confirm(command):
        response=await client.post('/api/v1/wealth/previews',json=command.model_dump())
        assert response.status_code==200,response.text
        view=response.json();base='/api/v1/wealth/previews/'+view['authorization_id']
        unconfirmed=await client.post(base+'/execute',json={})
        assert unconfirmed.status_code==(409 if command.plan_id else 403)
        assert unconfirmed.json()['detail']['code']==('PLAN_EXECUTION_REQUIRED' if command.plan_id else 'AUTHORIZATION_REQUIRED')
        assert (await client.post(base+'/confirm',json={'digest':view['digest'],
            'disclosure_version':1,'accepted':True})).status_code==200
        assert (await client.post(base+'/verify/demo',json={})).status_code==200
        return view,base
    sell=InvestmentCommand(operation_id='http-quick-lost-sell',direction='redeem',account_id=buy.account_id,
        product_id='B017',product_version=1,disclosure_version=1,units='1000.0000',command_version=2,redemption_mode='quick')
    view,base=await preview_confirm(sell)
    # 原操作号不得改成另一种赎回方式，即便原金额和账户相同。
    changed_mode=InvestmentCommand.model_validate(view['command']).model_copy(update={'redemption_mode':'standard'})
    mode_reply=await client.post('/api/v1/wealth/previews',json=changed_mode.model_dump())
    assert mode_reply.status_code==409
    assert mode_reply.json()['detail']['code']=='IDEMPOTENCY_CONFLICT'
    # 模拟业务时钟不能延长真实授权期限。
    from backend.app.models.wealth import WealthAuthorizationORM
    from backend.app.domain.time import utc_now
    from datetime import timedelta
    expired_view,expired_base=await preview_confirm(sell.model_copy(update={'operation_id':'http-quick-expired','units':'10.0000'}))
    async with store() as db:
        auth=await db.get(WealthAuthorizationORM,expired_view['authorization_id'])
        auth.expires_at=utc_now()-timedelta(seconds=1);await db.commit()
    expired_reply=await client.post(expired_base+'/execute',json={})
    assert expired_reply.status_code==409 and expired_reply.json()['detail']['code']=='AUTHORIZATION_EXPIRED'
    # 原报价净值行变价，旧确认必须拒绝，不能用新价格承接旧同意。
    changed=sell.model_copy(update={'operation_id':'http-quick-nav-change','units':'10.0000'})
    changed_reply=await client.post('/api/v1/wealth/previews',json=changed.model_dump())
    assert changed_reply.status_code==200
    changed_view=changed_reply.json()
    async with store() as db:
        price=await bank.latest_nav(db,'B017:1',await bank.business_now(db,buy.account_id))
        price_id,old_price=price.nav_id,price.price
        price.price=str(Decimal(old_price)+Decimal('0.01'));await db.commit()
    refused=await client.post('/api/v1/wealth/previews/'+changed_view['authorization_id']+'/confirm',
        json={'digest':changed_view['digest'],'disclosure_version':1,'accepted':True})
    assert refused.status_code==409 and refused.json()['detail']['code']=='QUOTE_CHANGED'
    async with store() as db:
        from backend.app.models.wealth import WealthNavORM
        price=await db.get(WealthNavORM,price_id);price.price=old_price;await db.commit()
    from backend.app.services.orchestration import wealth_task as tasks
    from backend.app.services.orchestration.wealth_task_worker import tick
    async with store() as db:
        task=await tasks.create_command(db,'U1','quick-buy-dependency',context['context_id'],
            InvestmentCommand.model_validate(view['command']),sell_then_buy=True)
        for _ in range(2):await tick(db,task['task_id'],local)
    local.lose_response=True
    unknown=await client.post(base+'/execute',json={})
    assert unknown.status_code==502 and unknown.json()['detail']['code']=='RESULT_UNKNOWN'
    async with store() as db:
        cash=(await db.get(AccountORM,buy.account_id)).balance_fen
        assert cash==initial+view['command']['quoted_cash_fen']
        assert await db.scalar(select(func.count()).select_from(WealthSettlementORM).where(WealthSettlementORM.kind=='quick_cash'))==1
    for _ in range(2):
        restored=await client.post(base+'/execute',json={})
        assert restored.status_code==200 and restored.json()['status']=='SETTLED'
    async with store() as db:
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==cash
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.operation_id==sell.operation_id))==1
        for _ in range(4):await tick(db,task['task_id'],local)
        graph=await tasks.view(db,await tasks.owned(db,'U1',task['task_id']))
        reread=next(node for node in graph['nodes'] if node['kind']=='REREAD_CASH')
        assert reread['status']=='COMPLETED'
        assert next(node for node in graph['nodes'] if node['kind']=='BUY_PLAN')['status']=='WAITING'
    oversized=sell.model_copy(update={'operation_id':'http-quick-insufficient','units':'999999.0000'})
    assert (await client.post('/api/v1/wealth/previews',json=oversized.model_dump())).status_code==409
    new_buy=buy.model_copy(update={'operation_id':'http-after-quick-new-buy','amount_fen':50000,
                                 'plan_id':'WPquick-followup','plan_version':1})
    async with store() as db:
        from backend.app.models.wealth import WealthPlanORM
        plan={'plan_id':'WPquick-followup','version':1,'stage':'BUY','request':{'kind':'new_cash','account_id':buy.account_id,'parent_task_id':task['task_id']},
              'items':[{'command':new_buy.model_dump(),'status':'AWAITING_CONFIRMATION'}],'profile_version':1,'goal_versions':{}}
        continued=await tasks.register_plan(db,'U1','quick-buy-dependency',context['context_id'],plan)
        assert continued['task_id']==task['task_id'] and continued['version']==2
        db.add(WealthPlanORM(plan_id=plan['plan_id'],user_id='U1',version=1,status='AWAITING_CONFIRMATION',data=plan))
        await db.commit()
    early=await client.post('/api/v1/wealth/plans/WPquick-followup/execute',json={'version':1})
    assert early.status_code==409 and early.json()['detail']['code']=='AUTHORIZATION_REQUIRED'
    new_view,new_base=await preview_confirm(new_buy)
    assert new_view['authorization_id']!=view['authorization_id']
    async with store() as db:
        for _ in range(2):await tick(db,task['task_id'],local)
    following=await client.post('/api/v1/wealth/plans/WPquick-followup/execute',json={'version':1})
    assert following.status_code==200,following.text
    assert following.json()['items'][0]['status']=='ACCEPTED'
    async with store() as db:
        from backend.app.services.bank.available_funds import held_cash
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==cash
        assert await held_cash(db,buy.account_id)==50000
        assert await db.scalar(select(func.count()).select_from(WealthSettlementORM).where(WealthSettlementORM.kind=='quick_cash'))==1
        for _ in range(3):await tick(db,task['task_id'],local)
        from backend.app.models.wealth import WealthOrderORM
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.operation_id==new_buy.operation_id))==1


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
async def test_quick_cash_atomic_unique_quota_and_quote_binding(store,pid,monkeypatch):
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'quick-test','mode':'snapshot','products':bank_products()}
        for p in payload['products']: p['rules']['dataset_version']='quick-test'
        assert (await research.import_dataset(db,payload))['ok']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:quick-test')
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-29T14:00:00+08:00'}
        await db.commit(); await research.select_context(db,'U1',ctx.context_id,1)
        await assess(db)
        buy=InvestmentCommand(operation_id='quick-buy-'+pid,direction='purchase',product_id=pid,product_version=1,disclosure_version=1,account_id=context['account_id'],amount_fen=2000000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        ctx=await db.get(WealthContextORM,context['context_id']); ctx.data={**ctx.data,'business_at':'2026-09-30T10:00:00+08:00'}; await db.commit()
        assert (await bank.settle(db,buy.operation_id))['status']=='SETTLED'
        initial=(await db.get(AccountORM,buy.account_id)).balance_fen
        lot=(await db.scalars(select(WealthLotORM).where(WealthLotORM.account_id==buy.account_id))).one()
        original_units,original_cost=lot.units_scaled,lot.cost_fen
        if pid=='B018':
            # 明确的隔离净值场景：已发布净值为1，核对真实成交恰好填满额度。
            from backend.app.models.wealth import WealthNavORM
            prices=list(await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id=='B018:1')))
            for price in prices:price.price='1.00000000'
            await db.commit()
        unbound=InvestmentCommand(operation_id='quick-redeem-'+pid,direction='redeem',product_id=pid,product_version=1,disclosure_version=1,account_id=buy.account_id,units='1000.0000',command_version=2,redemption_mode='quick')
        # 所有估值尚未发布或全部缺失：快赎拒绝，不占额度、不改现金份额。
        from backend.app.models.wealth import WealthNavORM, WealthOrderORM
        from backend.app.domain.time import utc_now
        from datetime import timedelta
        from sqlalchemy import delete
        prices=list(await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id==pid+':1')))
        original_publications={p.nav_id:p.available_at for p in prices}
        order_count=await db.scalar(select(func.count()).select_from(WealthOrderORM))
        for p in prices:p.available_at=utc_now()+timedelta(days=10)
        await db.commit()
        with pytest.raises(BankError) as unavailable:await bank.precheck(db,'U1',unbound)
        assert unavailable.value.code=='DATA_STALE'
        for p in prices:p.available_at=original_publications[p.nav_id]
        await db.commit()
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id==pid+':1'))
        with pytest.raises(BankError) as missing:await bank.precheck(db,'U1',unbound)
        assert missing.value.code=='DATA_STALE'
        await db.rollback()
        assert (await db.get(AccountORM,buy.account_id)).balance_fen==initial
        await db.refresh(lot)
        assert (lot.units_scaled,lot.cost_fen)==(original_units,original_cost)
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==order_count
        assert await db.scalar(select(func.count()).select_from(WealthQuickQuotaORM))==0
        preview=await bank.precheck(db,'U1',unbound)
        bound=InvestmentCommand.model_validate(preview['command'])
        # 普通赎回授权不能用于同一操作号的快赎，即使份额相同。
        from backend.app.integrations.wealth_authorization import WealthPolicyError
        ordinary = unbound.model_copy(update={'operation_id': 'standard-not-quick-'+pid, 'command_version': 1,
            'redemption_mode': 'standard'})
        ordinary_auth = await authorize(db, ordinary)
        different_mode = bound.model_copy(update={'operation_id': ordinary.operation_id})
        with pytest.raises(WealthPolicyError) as wrong_mode:
            await bank.accept_many(db, 'U1', [(different_mode, ordinary_auth)])
        assert wrong_mode.value.code == 'AUTHORIZATION_REQUIRED'
        await db.rollback()
        # 未提交任务可取消；旧授权失效，取消不会改变账务或占用额度。
        from backend.app.services.orchestration import wealth_task as tasks
        draft = bound.model_copy(update={'operation_id': 'quick-cancel-draft-'+pid})
        draft_auth = await authorize(db, draft)
        task = await tasks.create_command(db, 'U1', 'quick-cancel-session-'+pid, context['context_id'], draft)
        cancelled = await tasks.control(db, 'U1', task['task_id'], 'cancel',
            {'request_id': 'quick-cancel-control-'+pid, 'expected_version': task['version']})
        assert cancelled['status'] == 'CANCELLED'
        with pytest.raises(WealthPolicyError) as expired_draft:
            await bank.accept_many(db, 'U1', [(draft, draft_auth)])
        assert expired_draft.value.code == 'AUTHORIZATION_EXPIRED'
        await db.rollback()
        assert await db.get(WealthOrderORM, draft.operation_id) is None
        assert (await db.get(AccountORM, buy.account_id)).balance_fen == initial
        assert await db.scalar(select(func.count()).select_from(WealthQuickQuotaORM)) == 0
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
        await db.rollback()
        if pid=='B018':
            remainder=unbound.model_copy(update={'operation_id':'quick-exact-limit','units':'9000.0000'})
            remainder=InvestmentCommand.model_validate((await bank.precheck(db,'U1',remainder))['command'])
            assert remainder.quoted_cash_fen==900000
            result=(await bank.accept_many(db,'U1',[(remainder,await authorize(db,remainder))]))[0]
            assert result['status']=='SETTLED'
            await db.refresh(quota)
            assert quota.paid_fen==1000000
            balance=(await db.get(AccountORM,buy.account_id)).balance_fen
            too_much=unbound.model_copy(update={'operation_id':'quick-over-exact-limit','units':'0.0100'})
            with pytest.raises(BankError) as error:await bank.precheck(db,'U1',too_much)
            assert error.value.code=='QUICK_DAILY_LIMIT'
            await db.rollback()
            assert (await db.get(AccountORM,buy.account_id)).balance_fen==balance
            assert await db.scalar(select(func.count()).select_from(WealthSettlementORM).where(WealthSettlementORM.kind=='quick_cash'))==2
        if pid=='B017':
            from backend.app.services.bank import wealth_demo as demo
            monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED','true')
            current=await demo.current(db,'U1')
            await db.refresh(quota)
            paid=quota.paid_fen
            await demo.write(db,'U1','adjust',{'run_id':current['run_id'],'expected_version':current['epoch'],
                'request_id':'quota-preserving-fund-adjust','action':'credit','amount':'100.00'})
            assert (await db.scalars(select(WealthQuickQuotaORM))).one().paid_fen==paid
            ctx=await db.get(WealthContextORM,context['context_id'])
            ctx.data={**ctx.data,'business_at':'2026-10-01T00:04:59+08:00'};await db.commit()
            next_day=bound.model_copy(update={'operation_id':'quick-next-day','nav_quote_id':None,'quoted_cash_fen':None,
                'context_id':None,'context_epoch':None})
            with pytest.raises(BankError) as closed:await bank.precheck(db,'U1',next_day)
            assert closed.value.code=='QUICK_WINDOW_CLOSED';await db.rollback()
            ctx=await db.get(WealthContextORM,context['context_id'])
            ctx.data={**ctx.data,'business_at':'2026-10-01T00:05:00+08:00'};await db.commit()
            next_day=InvestmentCommand.model_validate((await bank.precheck(db,'U1',next_day))['command'])
            assert (await bank.accept_many(db,'U1',[(next_day,await authorize(db,next_day))]))[0]['status']=='SETTLED'
            quotas=list(await db.scalars(select(WealthQuickQuotaORM)))
            assert len(quotas)==2 and sum(row.paid_fen for row in quotas)==paid+next_day.quoted_cash_fen


@pytest.mark.asyncio
async def test_quick_multi_account_concurrency_shared_quota_and_transaction_fault(store,monkeypatch):
    from backend.app.services.bank import wealth_quick
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'quick-race','mode':'snapshot','products':bank_products()}
        for product in payload['products']:product['rules']['dataset_version']='quick-race'
        assert (await research.import_dataset(db,payload))['ok']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:quick-race')
        first=await db.get(WealthContextORM,context['context_id'])
        first.data={**first.data,'run_id':'RUNquick-race','business_at':'2026-09-29T14:00:00+08:00'}
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
            ctx=await db.get(WealthContextORM,cid);ctx.data={**ctx.data,'business_at':'2026-09-30T10:00:00+08:00'}
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
