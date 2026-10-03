"""当前测试轮次管理：双库隔离验证，不访问演示账务。"""
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store
from backend.app.services.bank import wealth_demo as demo, wealth_research as research
from backend.app.services.bank.transfer_service import BankError
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth_research import WealthContextORM


@pytest.mark.asyncio
async def test_demo_switch_owner_reset_adjust_replay_delete_tombstone(store, monkeypatch):
    async with store() as db:
        context = await research.initialize(db, 'U1')
        await db.commit()
        await research.select_context(db, 'U1', context['context_id'], 1)
        assert (await demo.current(db, 'U1'))['writable'] is False
        monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED', 'true')
        ctx = await db.get(WealthContextORM, context['context_id'])
        initial = (await db.get(AccountORM, ctx.account_id)).balance_fen
        body = {'run_id': ctx.context_id, 'expected_version': ctx.version, 'request_id': 'fund-credit-01', 'action': 'credit', 'amount': '20.01'}
        adjusted = await demo.write(db, 'U1', 'adjust', body)
        assert adjusted['balance_fen'] == initial + 2001
        assert await demo.write(db, 'U1', 'adjust', body) == adjusted
        with pytest.raises(BankError) as error:
            await demo.write(db, 'U1', 'adjust', {**body, 'amount': '30.01'})
        assert error.value.code == 'IDEMPOTENCY_CONFLICT'
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.account_id == context['account_id'])) == 1
        current = await demo.current(db, 'U1')
        reset = {'run_id': current['run_id'], 'expected_version': current['epoch'], 'request_id': 'reset-round-01'}
        result = await demo.write(db, 'U1', 'reset', reset)
        assert await demo.write(db, 'U1', 'reset', reset) == result
        assert result['balance_fen'] == 2000000 and result['account_id'] != context['account_id']
        assert (await db.get(AccountORM, 'A1')).balance_fen == 2000000
        with pytest.raises(BankError) as error: await demo.require_active(db, context['account_id'])
        assert error.value.code == 'ARCHIVED_RUN'
        rows = (await demo.archives(db, 'U1'))['items']
        assert len(rows) == 1
        assert rows[0]['counts']['transactions'] == 1 and rows[0]['deletable']
        with pytest.raises(BankError) as error:
            await demo.deletion_preview(db, 'U2', [{'run_id': rows[0]['run_id'], 'expected_version': rows[0]['expected_version']}])
        assert error.value.code == 'DEMO_SCOPE_DENIED'
        items = [{'run_id': row['run_id'], 'expected_version': row['expected_version']} for row in rows]
        preview = await demo.deletion_preview(db, 'U1', items)
        deletion = {'runs': items, 'request_id': 'delete-round-01', 'preview_digest': preview['preview_digest'], 'confirmed': True}
        deleted = await demo.write(db, 'U1', 'delete', deletion)
        assert deleted['deleted_count'] == 1
        assert await demo.write(db, 'U1', 'delete', deletion) == deleted
        assert await db.get(AccountORM, context['account_id']) is None
        assert await db.get(AccountORM, result['account_id']) is not None
        assert (await db.get(AccountORM, 'A2')).balance_fen == 2000000
        with pytest.raises(BankError) as error: await demo.context_owned(db, 'U1', context['context_id'])
        assert error.value.code == 'GONE'
        with pytest.raises(BankError) as error: await demo.context_owned(db, 'U1', 'never-existed')
        assert error.value.code == 'NOT_FOUND'


@pytest.mark.asyncio
async def test_demo_amount_and_current_scope_cannot_touch_ordinary(store, monkeypatch):
    monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED', 'true')
    async with store() as db:
        context = await research.initialize(db, 'U1')
        await db.commit(); await research.select_context(db, 'U1', context['context_id'], 1)
        for amount in ['-1', '0', '0.001', 'NaN', '20001']:
            body = {'run_id': context['context_id'], 'expected_version': 1, 'request_id': 'invalid-funds-'+amount,
                'action': 'debit', 'amount': amount}
            with pytest.raises(BankError): await demo.write(db, 'U1', 'adjust', body)
            await db.rollback()
        assert (await db.get(AccountORM, 'A1')).balance_fen == 2000000
        assert await db.scalar(select(func.count()).select_from(TransactionORM)) == 0


@pytest.mark.asyncio
async def test_archive_delete_blocks_lease_then_removes_task_history_and_returns_gone(store,monkeypatch):
    from datetime import timedelta
    from backend.app.domain.time import utc_now
    from backend.app.models.wealth import WealthConversationORM,WealthTaskNodeORM,WealthTaskControlORM
    from backend.app.services.orchestration import wealth_task as tasks
    from backend.app.schemas.wealth import InvestmentCommand
    from backend.app.integrations.wealth_authorization import WealthPolicyError
    monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED','true')
    async with store() as db:
        ctx=await research.initialize(db,'U1');await db.commit()
        await research.select_context(db,'U1',ctx['context_id'],1)
        cmd=InvestmentCommand(operation_id='delete-task-old-operation',direction='purchase',account_id=ctx['account_id'],product_id='S001',product_version=1,disclosure_version=1,amount_fen=100000)
        db.add(WealthConversationORM(session_id='delete-old-session',user_id='U1',version=1,
            data={'context_id':ctx['context_id'],'history':[{'user':'虚构测试','reply':{'command':cmd.model_dump()}}]}))
        await db.commit()
        task=await tasks.create_command(db,'U1','delete-old-session',ctx['context_id'],cmd)
        await tasks.control(db,'U1',task['task_id'],'pause',{'expected_version':1,'request_id':'delete-task-pause'})
        current=await demo.current(db,'U1')
        reset=await demo.write(db,'U1','reset',{'run_id':current['run_id'],'expected_version':current['expected_version'],'request_id':'delete-task-reset'})
        old=(await demo.archives(db,'U1'))['items'][0]
        runs=[{'run_id':old['run_id'],'expected_version':old['expected_version']}]
        preview=await demo.deletion_preview(db,'U1',runs)
        assert preview['runs'][0]['counts']['tasks']==1 and preview['runs'][0]['counts']['conversations']==1
        node=await db.scalar(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task['task_id'],WealthTaskNodeORM.kind=='SUBMIT'))
        node_id=node.node_id
        node.lease_until=utc_now()+timedelta(seconds=90);await db.commit()
        blocked = (await demo.archives(db, 'U1'))['items'][0]
        assert not blocked['deletable'] and blocked['blocking_reasons']
        deletion={'runs':runs,'preview_digest':preview['preview_digest'],'confirmed':True,'request_id':'delete-task-confirm'}
        with pytest.raises(BankError) as error:await demo.write(db,'U1','delete',deletion)
        assert error.value.code=='DELETION_BLOCKED';await db.rollback()
        assert await db.get(AccountORM,ctx['account_id']) is not None
        node=await db.get(WealthTaskNodeORM,node_id);node.lease_until=None;await db.commit()
        deleted=await demo.write(db,'U1','delete',deletion)
        assert task['task_id'] in deleted['deleted_task_ids']
        assert await db.get(WealthConversationORM,'delete-old-session') is None
        assert await db.scalar(select(func.count()).select_from(WealthTaskControlORM))==0
        with pytest.raises(WealthPolicyError) as error:await tasks.owned(db,'U1',task['task_id'])
        assert error.value.code=='GONE'
        with pytest.raises(WealthPolicyError) as error:await tasks.owned(db,'U2',task['task_id'])
        assert error.value.code=='NOT_FOUND'
        assert await db.get(AccountORM,reset['account_id']) is not None


@pytest.mark.asyncio
async def test_reset_frozen_opening_holdings_and_orders_without_inheriting_authorization(store, monkeypatch):
    from datetime import date
    from backend.app.domain.time import utc_now
    from backend.app.models.wealth import WealthOrderORM, WealthLotORM, WealthAuthorizationORM
    from backend.app.services.bank.wealth_fixture_baseline import freeze
    monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED', 'true')
    async with store() as db:
        result = await research.initialize(db, 'U1'); await db.commit()
        await research.select_context(db, 'U1', result['context_id'], 1)
        ctx = await db.get(WealthContextORM, result['context_id'])
        db.add(WealthOrderORM(operation_id='initial-fixture-order', user_id='U1', account_id=ctx.account_id,
            version_id='MF001:1', direction='purchase', amount_fen=100000, units_scaled=10000000,
            param_digest='initial-fixture-digest', authorization_id='fixture-no-human-authorization',
            plan_version=1, status='SETTLED', accepted_at=utc_now(), pricing_date=date(2026,9,29),
            confirm_date=date(2026,9,30), cash_date=date(2026,9,30), details={'opening_fixture': True}))
        await db.flush()
        db.add(WealthLotORM(lot_id='initial-fixture-lot',operation_id='initial-fixture-order',user_id='U1',
            account_id=ctx.account_id,version_id='MF001:1',units_scaled=10000000,cost_fen=100000,
            acquired_date=date(2026,9,30),unlock_date=date(2026,9,30)))
        await db.flush(); await freeze(db, ctx); await db.commit()
        fixture_version = ctx.data['fixture_version']
        lot = await db.get(WealthLotORM, 'initial-fixture-lot'); lot.units_scaled = 3000000
        await db.commit()
        reset = await demo.write(db, 'U1', 'reset', {'run_id':ctx.context_id,'expected_version':ctx.version,'request_id':'reset-frozen-fixture'})
        new = await db.get(WealthContextORM, reset['run_id'])
        assert new.data['fixture_version'] == fixture_version
        lots = list(await db.scalars(select(WealthLotORM).where(WealthLotORM.account_id==reset['account_id'])))
        assert len(lots)==1 and lots[0].units_scaled==10000000 and lots[0].cost_fen==100000
        assert lots[0].lot_id!='initial-fixture-lot'
        order = await db.get(WealthOrderORM, lots[0].operation_id)
        assert order.status=='SETTLED' and order.details['human_authorization_inherited'] is False
        assert await db.get(WealthAuthorizationORM, order.authorization_id) is None
        assert (await db.get(AccountORM, reset['account_id'])).balance_fen==2000000
        assert (await db.get(WealthLotORM, 'initial-fixture-lot')).units_scaled==3000000


@pytest.mark.asyncio
async def test_mixed_history_split_uses_explicit_accounts_and_is_idempotent(store):
    from backend.app.models.wealth import WealthConversationORM
    from backend.app.services.bank.wealth_history_migration import split
    async with store() as db:
        created=await research.initialize(db,'U1');await db.commit()
        context=await db.get(WealthContextORM,created['context_id'])
        context_id=context.context_id
        ordinary={'user':'历史普通账本需求','reply':{'pending':{'account_id':'A1'}}}
        sandbox={'user':'历史模拟需求','reply':{'command':{'account_id':context.account_id}}}
        unknown={'user':'继续','reply':{'kind':'clarification'}}
        db.add(WealthConversationORM(session_id='mixed-history-source',user_id='U1',version=2,
            data={'context_id':context.context_id,'history':[sandbox],'history_archive':[ordinary,unknown]}))
        await db.commit()
        before={row.account_id:row.balance_fen for row in await db.scalars(select(AccountORM))}
        dry=await split(db)
        assert dry['rows'][0]['partitions']=={'legacy-v1':1,'legacy-unassigned':1,context_id:1}
        assert (await db.get(WealthConversationORM,'mixed-history-source')).data['history_archive']
        applied=await split(db,apply=True)
        assert applied['changed_money_or_units'] is False
        assert (await split(db,apply=True))['rows']==[]
        rows=list(await db.scalars(select(WealthConversationORM)))
        assert sum(len(row.data.get('history',[])) for row in rows)==3
        assert all(not row.data.get('history_archive') for row in rows)
        readonly=[row for row in rows if row.data.get('legacy_read_only')]
        assert len(readonly)==2 and {row.data['context_id'] for row in readonly}=={'legacy-v1','legacy-unassigned'}
        assert before=={row.account_id:row.balance_fen for row in await db.scalars(select(AccountORM))}
