"""Independent evidence, cashflow, scenario and permission boundary acceptance tests."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.test_bills import db, tx, QUERY, NOW
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.subscription import SubscriptionORM
from backend.app.models.wealth import WealthCashHoldORM
from backend.app.models.workflow import TransferTaskORM, TransferPlanORM, TransferOperationORM
from backend.app.schemas.bills import BillQuery, BillAnalysis
from backend.app.schemas.bill_insights import CashflowScenario, CashflowProjection, SubscriptionInsights
from backend.app.services.bank.bill_service import analyze, TZ
from backend.app.services.bank.bill_subscriptions import subscription_insights
from backend.app.services.bank.bill_cashflow import project_cashflow
from backend.app.services.bank.transfer_service import BankError


def sub(key='S1', account='A1', merchant='音乐', amount=1500, **kwargs):
    return SubscriptionORM(subscription_id=key, user_id=kwargs.pop('user', 'U1'), debit_account_id=account,
                           merchant_name=merchant, amount_fen=amount, currency=kwargs.pop('currency','CNY'),
                           billing_cycle='MONTHLY', next_charge_at=kwargs.pop('when', datetime(2026, 10, 20, 4, tzinfo=timezone.utc)),
                           status=kwargs.pop('status','ACTIVE'), state_version=1, **kwargs)


async def ledger_counts(db):
    return (await db.scalar(select(func.count()).select_from(TransactionORM)),
            await db.scalar(select(func.sum(AccountORM.balance_fen))),
            await db.scalar(select(func.count()).select_from(TransferTaskORM)))


@pytest.mark.asyncio
async def test_exact_change_decomposition_and_evidence(db):
    db.add_all([tx('old1', 100, when='2026-08-01T04:00:00'), tx('old2', 101, when='2026-08-02T04:00:00'),
                tx('new1', 200), tx('new2', 200), tx('new3', 200),
                tx('new-large', 100001, category='shopping', counterparty='新商城'),
                tx('gone', 999, category='transport', counterparty='旧出行', when='2026-08-03T04:00:00'),
                tx('foreign', 999999, account='A3')])
    await db.commit()
    before = await ledger_counts(db)
    result = await analyze(db, 'U1', QUERY, now=NOW)
    explanation = result['change_explanation']
    assert explanation['total_change_fen'] == result['change_fen'] == 99401
    assert explanation['frequency_effect_fen'] + explanation['unit_amount_effect_fen'] + explanation['entry_exit_effect_fen'] == 99401
    driver = next(d for d in explanation['drivers'] if d['merchant'] == '商户')
    assert driver['frequency_effect_fen'] == 101
    assert driver['unit_amount_effect_fen'] == 298
    assert len(driver['current_evidence']) == 3 and len(driver['previous_evidence']) == 2
    assert all(e['tx_id'] != 'foreign' for d in explanation['drivers'] for e in d['current_evidence'])
    assert next(d for d in explanation['drivers'] if d['merchant'] == '新商城')['new_large_single']
    BillAnalysis.model_validate(result)
    assert await ledger_counts(db) == before


@pytest.mark.asyncio
async def test_evidence_limit_does_not_truncate_totals(db):
    db.add_all([tx(f'b{i}', 1) for i in range(501)])
    await db.commit()
    result = await analyze(db, 'U1', QUERY, now=NOW)
    driver = result['change_explanation']['drivers'][0]
    assert driver['current_count'] == 501 and driver['current_fen'] == 501
    assert len(driver['current_evidence']) == 5


@pytest.mark.asyncio
async def test_subscription_price_duplicates_and_ambiguous_relations(db):
    db.add_all([sub(), sub('S2'), sub('S3', account='A3', user='U2'),
                tx('old-sub', 1000, 'subscription', counterparty='音乐', when='2026-08-10T04:00:00'),
                tx('new-sub1', 1500, 'subscription', counterparty='音乐', when='2026-09-10T04:00:00'),
                tx('new-sub2', 1500, 'subscription', counterparty='音乐', when='2026-09-10T04:02:00'),
                tx('unlinked', 2500, 'subscription', counterparty='未关联商户')])
    await db.commit()
    before = await ledger_counts(db)
    result = await subscription_insights(db, 'U1', BillQuery(date='2026-09', category='dining'), now=NOW)
    item = next(i for i in result['items'] if i['merchant'] == '音乐')
    assert set(item['flags']) >= {'charge_increase', 'possible_duplicate', 'ambiguous_link', 'multiple_active'}
    assert item['current_fen'] == 3000 and item['configured_monthly_fen'] == 3000
    assert item['subscription_ids'] == ['S1', 'S2']
    assert next(i for i in result['items'] if i['merchant'] == '未关联商户')['flags'] == ['unlinked_charge']
    SubscriptionInsights.model_validate(result)
    assert await ledger_counts(db) == before
    with pytest.raises(BankError):
        await subscription_insights(db, 'U2', BillQuery(date='2026-09', account_id='A1'), now=NOW)


@pytest.mark.asyncio
async def test_subscription_after_cancellation_and_plan_mismatch(db):
    db.add_all([sub('C', merchant='旧订阅', status='CANCELLED', cancelled_at=datetime(2026,9,1,tzinfo=timezone.utc)),
                sub('A', merchant='新订阅', amount=3000),
                tx('after', 1500, 'subscription', counterparty='旧订阅'),
                tx('different', 2000, 'subscription', counterparty='新订阅')])
    await db.commit()
    result = await subscription_insights(db, 'U1', QUERY, now=NOW)
    old = next(i for i in result['items'] if i['merchant']=='旧订阅')
    assert 'charge_after_cancellation' in old['flags'] and old['active_subscription_ids'] == []
    assert 'plan_differs' in next(i for i in result['items'] if i['merchant']=='新订阅')['flags']


async def history(db):
    for month in (7, 8, 9):
        db.add(tx(f'salary{month}', 100000, 'salary', category='salary', when=f'2026-{month:02}-25T04:00:00', counterparty='雇主'))
        for day in (1, 2, 3):
            db.add(tx(f'food{month}{day}', 3000, when=f'2026-{month:02}-{day:02}T04:00:00'))
    await db.commit()


@pytest.mark.asyncio
async def test_cashflow_components_scenarios_and_no_mutations(db):
    await history(db)
    db.add_all([sub(), WealthCashHoldORM(operation_id='hold', account_id='A1', amount_fen=5000, status='HELD')])
    await db.commit()
    before = await ledger_counts(db)
    request = CashflowScenario(days=30, reductions=[{'category':'dining','percent':50}], excluded_subscription_ids=['S1'])
    result = await project_cashflow(db, 'U1', request, now=NOW)
    assert result['opening_balance_fen'] == 200000 and result['opening_available_fen'] == 195000
    assert result['held_fen'] == 5000 and result['planned_outflow_fen'] == 1500
    assert result['estimated_income_fen'] == 100000
    assert result['baseline_end_fen'] == 195000 - 1500 + 100000 - result['estimated_variable_spending_fen']
    expected_reduction = result['spending_baselines'][0]['estimated_total_fen'] - result['spending_baselines'][0]['scenario_total_fen']
    assert result['scenario_saving_fen'] == 1500 + expected_reduction
    assert result['scenario_end_fen'] - result['baseline_end_fen'] == result['scenario_saving_fen']
    assert len(result['timeline']) == 31
    assert result['end_exclusive'] == NOW + timedelta(days=30)
    for p in result['timeline']:
        assert p['historical_low_balance_fen'] <= p['baseline_balance_fen'] <= p['historical_high_balance_fen']
    CashflowProjection.model_validate(result)
    assert await ledger_counts(db) == before
    assert (await db.get(SubscriptionORM,'S1')).status == 'ACTIVE'


@pytest.mark.asyncio
async def test_cashflow_sparse_history_credit_and_ownership(db):
    db.add(AccountORM(account_id='C1', user_id='U1', account_type='credit', card_number='credit', balance_fen=999999))
    db.add(tx('one-salary', 999999, 'salary'))
    db.add(tx('foreign-food', 999999, account='A3'))
    await db.commit()
    result = await project_cashflow(db, 'U1', CashflowScenario(days=7), now=NOW)
    assert result['opening_available_fen'] == 200000
    assert result['estimated_income_fen'] == result['estimated_variable_spending_fen'] == 0
    assert result['notices'] and len(result['timeline']) == 8
    for user, account, status in [('U1','C1',422), ('U2','A1',404)]:
        with pytest.raises(BankError) as error:
            await project_cashflow(db,user,CashflowScenario(account_id=account),now=NOW)
        assert error.value.status_code == status


@pytest.mark.asyncio
async def test_cashflow_scenario_validation_and_cancelled_subscription(db):
    db.add_all([sub(), sub('cancelled', status='CANCELLED')])
    await db.commit()
    for ids in [['unknown'],['cancelled']]:
        with pytest.raises(BankError) as error:
            await project_cashflow(db,'U1',CashflowScenario(excluded_subscription_ids=ids),now=NOW)
        assert error.value.code == 'SCENARIO_SUBSCRIPTION_NOT_FOUND'
    with pytest.raises(BankError):
        await project_cashflow(db,'U1',CashflowScenario(reductions=[{'category':'dining','percent':1},{'category':'dining','percent':2}]),now=NOW)


@pytest.mark.asyncio
async def test_month_end_plan_recurrence_clamps_without_drift(db):
    db.add(sub(when=datetime(2026,1,31,4,tzinfo=timezone.utc)))
    await db.commit()
    result = await project_cashflow(db,'U1',CashflowScenario(days=62), now=datetime(2026,2,1,tzinfo=TZ))
    assert [e['at'].date().isoformat() for e in result['events']] == ['2026-02-28','2026-03-31']
    assert result['planned_outflow_fen'] == 3000


@pytest.mark.asyncio
async def test_salary_duplicate_or_variable_dates_not_inferred(db):
    await history(db)
    db.add(tx('bonus',1000,'salary', counterparty='雇主',when='2026-09-26T04:00:00'))
    await db.commit()
    result = await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert result['estimated_income_fen'] == 0


@pytest.mark.asyncio
async def test_intraday_fraction_and_reduction_rounding(db):
    await history(db)
    result = await project_cashflow(db,'U1',CashflowScenario(days=1, reductions=[{'category':'dining','percent':100}]),now=NOW)
    assert result['estimated_variable_spending_fen'] == 293  # 27000/92: a FULL day spanning two partial dates, exact cumulative cents rounding
    assert result['scenario_saving_fen'] == 293
    assert result['spending_baselines'][0]['scenario_total_fen'] == 0


def scheduled(db, key, sender, receiver, amount, phase='WAITING_EXECUTION', current=True):
    db.add(TransferTaskORM(task_id=key,user_id='U1',phase=phase,current_plan_version=1))
    db.add(TransferPlanORM(plan_id=key+'plan', task_id=key,plan_version=1,is_current=current,
                           params={'scheduled_at':'2026-10-20T12:00:00+08:00'}))
    db.add(TransferOperationORM(operation_id=key+'op',task_id=key,step_id=key+'step',plan_version=1,
                               status='READY',from_account=sender,to_account=receiver,amount_fen=amount,param_digest='x'))


@pytest.mark.asyncio
async def test_scheduled_current_authorized_plans_and_internal_transfer(db):
    scheduled(db,'external','A1','A3',1000)
    scheduled(db,'internal','A1','A2',2000)
    scheduled(db,'cancelled','A1','A3',999999,phase='FINISHED')
    scheduled(db,'draft','A1','A3',999999,phase='WAITING_CONFIRMATION')
    scheduled(db,'obsolete','A1','A3',999999,current=False)
    await db.commit()
    result = await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert result['planned_outflow_fen'] == 1000
    assert len(result['events']) == 1
    single = await project_cashflow(db,'U1',CashflowScenario(account_id='A2'),now=NOW)
    assert single['baseline_end_fen'] == 102000


@pytest.mark.asyncio
async def test_negative_path_and_low_funds(db):
    (await db.get(AccountORM,'A1')).balance_fen=10
    db.add(sub(amount=150000))
    await db.commit()
    result = await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert result['first_negative_date'] == '2026-10-20'
    assert result['minimum_baseline_fen'] == -49990


@pytest.mark.asyncio
async def test_enhancement_http_and_nova_readonly_boundary(db,monkeypatch):
    from backend.app.main import app
    from backend.app.database.connection import get_db
    from backend.app.middleware.auth import create_access_token
    from backend.app.services.ai.nova import execute_tool
    from backend.app.services.ai.nova_tools import ToolContext
    from backend.app.schemas.nova import PageContext, BillFilters
    await history(db)
    db.add(sub())
    await db.commit()
    before = await ledger_counts(db)
    async def override(): yield db
    app.dependency_overrides[get_db] = override
    headers={'Authorization':'Bearer '+create_access_token({'sub':'U1'})}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.post('/api/v1/bills/cashflow',json={})).status_code==401
            assert (await client.get('/api/v1/bills/subscription-insights?date=2026-09',headers=headers)).status_code==200
            response=await client.post('/api/v1/bills/cashflow',headers=headers,json={'days':30})
            assert response.status_code==200, response.text
            for body in [{'days':0},{'days':63},{'reductions':[{'category':'dining','percent':101}]},{'user_id':'U2'},{'days':1.5}]:
                assert (await client.post('/api/v1/bills/cashflow',headers=headers,json=body)).status_code==422
            assert (await client.post('/api/v1/bills/cashflow',headers=headers,json={'account_id':'A3'})).status_code==404
        ctx=ToolContext('U1',db,PageContext(page='insights',filters=BillFilters(account_id='A1')))
        response=await execute_tool({'function':{'name':'project_cashflow','arguments':'{"days":7}'}},ctx)
        assert response['data']['account_id']=='A1'
        response=await execute_tool({'function':{'name':'project_cashflow','arguments':'{"user_id":"U2"}'}},ctx)
        assert response.get('error')
        assert await ledger_counts(db)==before
    finally: app.dependency_overrides.pop(get_db,None)


@pytest.mark.asyncio
async def test_cancel_preview_requires_confirmation_and_recomputes_plan(db, monkeypatch):
    from backend.app.services.bank import subscription_service as service
    monkeypatch.setattr(service, 'AsyncSessionLocal', async_sessionmaker(db.bind, expire_on_commit=False))
    db.add(sub())
    await db.commit()
    baseline = await project_cashflow(db, 'U1', CashflowScenario(), now=NOW)
    assert baseline['planned_outflow_fen'] == 1500
    preview = await service.preview_subscription_cancellation(subscription_id='S1', user_id='U1')
    db.expire_all()
    assert (await db.get(SubscriptionORM, 'S1')).status == 'ACTIVE'
    with pytest.raises(BankError) as error:
        await service.cancel_subscription(subscription_id='S1', user_id='U1', state_version=1)
    assert error.value.code == 'CONFIRMATION_REQUIRED'
    await db.rollback()
    await service.cancel_subscription(subscription_id='S1', user_id='U1', state_version=1, confirmation_token=preview['confirmation_token'])
    db.expire_all()
    result = await project_cashflow(db, 'U1', CashflowScenario(), now=NOW)
    assert result['planned_outflow_fen'] == 0
    with pytest.raises(BankError):
        await project_cashflow(db, 'U1', CashflowScenario(excluded_subscription_ids=['S1']), now=NOW)


@pytest.mark.asyncio
async def test_non_cny_subscription_unsupported_scope_not_silently_projected(db):
    db.add(sub(currency='USD'))
    await db.commit()
    result = await project_cashflow(db, 'U1', CashflowScenario(), now=NOW)
    assert result['planned_outflow_fen'] == 0
    assert any('不是有效人民币月度计划' in n for n in result['notices'])


@pytest.mark.asyncio
async def test_nova_offline_forecast_and_reduction_calls_registered_tool(db):
    from backend.app.services.ai.nova import offline_business
    from backend.app.services.ai.nova_tools import ToolContext
    from backend.app.schemas.nova import PageContext
    await history(db)
    context = ToolContext('U1', db, PageContext(page='insights'))
    response = await offline_business('未来7天现金流情景减少餐饮20%', context)
    assert response['tool_calls'][0]['name'] == 'project_cashflow'
    assert response['tool_calls'][0]['result']['data']['days'] == 7
    assert response['tool_calls'][0]['result']['data']['scenario']['reductions'] == [{'category':'dining','percent':20}]
    assert '情景差额' in response['reply']
