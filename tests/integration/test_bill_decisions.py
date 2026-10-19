"""Acceptance tests for user decisions, refund caps, reversible identity and forecasts."""
import json
from datetime import datetime,timezone,timedelta
import pytest
from sqlalchemy import select,func
from tests.integration.test_bills import db,tx,NOW,QUERY
from tests.integration.test_bill_foundations import sync,refund_chain,recurring_history
from backend.app.models import TransactionORM,AccountORM
from backend.app.models.bill_foundation import BillTransactionMetaORM as Meta,BillMerchantORM as Merchant,BillRecurringStreamORM as Stream
from backend.app.models.bill_features import BillForecastSnapshotORM as Snapshot,MerchantRefundPartORM as Part
from backend.app.schemas.bills import BillQuery
from backend.app.schemas.bill_features import ClassificationUpdate,BudgetUpdate,AnomalyReview,RecurringPlan,MerchantSplit,SplitSpec
from backend.app.schemas.bill_insights import CashflowScenario
from backend.app.services.bank import bill_classification as classifications,bill_budgets as budgets,bill_reviews as reviews,bill_merchants as merchants,bill_schedules as schedules,bill_forecasts as forecasts,bill_foundation as foundation
from backend.app.services.bank.bill_service import analyze,transactions,TZ
from backend.app.services.bank.bill_cashflow import project_cashflow
from backend.app.services.bank.bill_recurring import list_recurring
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
async def test_corrected_categories_and_rule_apply_to_reports_and_linked_refunds(db):
    await refund_chain(db);await sync(db)
    original=await db.get(TransactionORM,'original');raw=(original.category,original.amount_fen)
    await classifications.correct(db,'U1','original',ClassificationUpdate(category='transport',apply_to_merchant=True,expected_version=0))
    await db.commit()
    report=await analyze(db,'U1',QUERY,now=NOW)
    assert report['categories'][0]['category']=='transport' and report['categories'][0]['refund_fen']==1000
    detail=(await foundation.metadata_views(db,'U1',['refund']))['refund']
    assert detail['effective_category']==detail['original_category']=='transport'
    assert (original.category,original.amount_fen)==raw
    db.add(tx('new',100,category='shopping',counterparty=original.counterparty));await db.commit();await sync(db)
    assert (await foundation.metadata_views(db,'U1',['new']))['new']['effective_category']=='transport'
    await classifications.remove_rule(db,'U1',(await db.get(Meta,'original')).merchant_id,1);await db.commit()
    assert (await foundation.metadata_views(db,'U1',['new']))['new']['effective_category']=='shopping'
    await classifications.correct(db,'U1','original',ClassificationUpdate(category=None,expected_version=1));await db.commit()
    assert (await foundation.metadata_views(db,'U1',['original']))['original']['effective_category']=='dining'


@pytest.mark.asyncio
async def test_suggestion_does_not_silently_reclassify_ledger_and_versions_are_checked(db):
    db.add(tx('ride',100,category='shopping',counterparty='滴滴出行'));await db.commit();await sync(db)
    view=(await foundation.metadata_views(db,'U1',['ride']))['ride']
    assert view['suggested_category']=='transport' and view['effective_category']=='shopping'
    await classifications.correct(db,'U1','ride',ClassificationUpdate(category='transport',expected_version=0));await db.commit()
    with pytest.raises(BankError) as error:await classifications.correct(db,'U1','ride',ClassificationUpdate(category='other',expected_version=0))
    assert error.value.code=='STALE_CLASSIFICATION'
    with pytest.raises(BankError):await classifications.correct(db,'U2','ride',ClassificationUpdate(category='other',expected_version=1))


@pytest.mark.asyncio
async def test_budget_actual_net_consumption_excludes_transfers_and_wealth(db):
    db.add_all([tx('purchase',9000),tx('refund',1000,'refund'),tx('transfer',900000,'transfer_out'),tx('wealth',999999,'investment_purchase')]);await db.commit()
    await budgets.save(db,'U1',BudgetUpdate(month='2026-09',amount_fen=10000))
    await db.commit();result=await budgets.summary(db,'U1','2026-09',now=NOW)
    item=result['items'][0]
    assert item['spent_fen']==8000 and item['remaining_fen']==2000 and item['status']=='warning'
    db.add(tx('extra',3000));await db.commit()
    assert (await budgets.summary(db,'U1','2026-09',now=NOW))['items'][0]['status']=='exceeded'
    with pytest.raises(BankError):await budgets.save(db,'U1',BudgetUpdate(month='2026-09',amount_fen=20000,expected_version=0))
    with pytest.raises(BankError):await budgets.save(db,'U2',BudgetUpdate(month='2026-09',account_id='A1',amount_fen=10000))
    assert (await budgets.summary(db,'U2','2026-09',now=NOW))['items']==[]


def plan(**kwargs):
    return RecurringPlan(**{'name':'保险','account_id':'A1','kind':'expense','frequency':'MONTHLY','amount_fen':12000,'anchor_at':datetime(2026,10,20,4,tzinfo=timezone.utc),**kwargs})


@pytest.mark.asyncio
async def test_budget_projects_known_periodic_spending_and_marks_sparse_history(db):
    await schedules.save_plan(db,'U1',plan())
    await budgets.save(db,'U1',BudgetUpdate(month='2026-10',amount_fen=10000));await db.commit()
    item=(await budgets.summary(db,'U1','2026-10',now=NOW))['items'][0]
    assert item['spent_fen']==0 and item['projected_spending_fen']==12000 and item['status']=='projected_exceeded'
    assert not item['projection_has_variable_baseline']


@pytest.mark.asyncio
async def test_review_keeps_history_and_changed_evidence_reopens_anomaly(db):
    db.add(tx('large',100000));await db.commit()
    report=await analyze(db,'U1',QUERY,now=NOW);item=report['anomalies'][0]
    body=AnomalyReview(query=QUERY,as_of=NOW,anomaly_id=item['anomaly_id'],fingerprint=item['fingerprint'],status='normal',note='本人购买',expected_version=0)
    await reviews.save_review(db,'U1',body);await db.commit()
    assert (await analyze(db,'U1',QUERY,now=NOW))['anomalies'][0]['review_status']=='normal'
    with pytest.raises(BankError) as stale:await reviews.save_review(db,'U1',body)
    assert stale.value.code=='STALE_REVIEW'
    (await db.get(TransactionORM,'large')).amount_fen=200000;await db.commit() # simulate an upstream corrected fact
    changed=(await analyze(db,'U1',QUERY,now=NOW))['anomalies'][0]
    assert changed['review_status']=='pending' and changed['evidence_changed']
    with pytest.raises(BankError):await reviews.save_review(db,'U1',body)


@pytest.mark.asyncio
async def test_merchant_preview_stale_guard_undo_and_split(db):
    db.add_all([tx('a',100,counterparty='甲商户'),tx('b',200,counterparty='乙商户')]);await db.commit();await sync(db)
    target,source=(await db.get(Meta,'a')).merchant_id,(await db.get(Meta,'b')).merchant_id
    preview=await merchants.preview(db,'U1',target,source)
    await foundation.add_alias(db,'U1',source,'乙新名');await db.commit()
    with pytest.raises(BankError) as stale:await merchants.merge(db,'U1',target,source,preview['preview_digest'])
    assert stale.value.code=='MERGE_PREVIEW_CHANGED'
    preview=await merchants.preview(db,'U1',target,source)
    applied=await merchants.merge(db,'U1',target,source,preview['preview_digest']);await db.commit()
    assert (await db.get(Meta,'b')).merchant_id==target
    await merchants.undo(db,'U1',applied['event_id']);await db.commit()
    assert (await db.get(Meta,'b')).merchant_id==source
    assert (await merchants.undo(db,'U1',applied['event_id']))['restored']
    split_preview=await merchants.split_preview(db,'U1',source,SplitSpec(name='独立商户',tx_ids=['b']))
    result=await merchants.split(db,'U1',source,MerchantSplit(name='独立商户',tx_ids=['b'],preview_digest=split_preview['preview_digest']));await db.commit()
    assert (await db.get(Meta,'b')).merchant_id==result['merchant_id']
    db.add(tx('future-split-merchant',100,counterparty='独立商户'));await db.commit()
    await foundation.sync_users(db,['U1'],changed_tx_ids=['future-split-merchant'],now=NOW);await db.commit()
    assert (await db.get(Meta,'future-split-merchant')).merchant_id==result['merchant_id']
    with pytest.raises(BankError):await merchants.split_preview(db,'U2',target)


@pytest.mark.asyncio
async def test_merge_undo_refuses_new_merchant_facts(db):
    db.add_all([tx('a',100,counterparty='甲'),tx('b',100,counterparty='乙')]);await db.commit();await sync(db)
    a,b=(await db.get(Meta,'a')).merchant_id,(await db.get(Meta,'b')).merchant_id
    applied=await merchants.merge(db,'U1',a,b);await db.commit()
    db.add(tx('new',100,counterparty='甲'));await db.commit();await foundation.sync_users(db,['U1'],changed_tx_ids=['new']);await db.commit()
    with pytest.raises(BankError) as stale:await merchants.undo(db,'U1',applied['event_id'])
    assert stale.value.code=='MERGE_STATE_CHANGED'


@pytest.mark.asyncio
@pytest.mark.parametrize('frequency,dates',[
    ('WEEKLY',['2026-09-29','2026-10-06','2026-10-13']),
    ('QUARTERLY',['2026-01-25','2026-04-25','2026-07-25']),
    ('YEARLY',['2023-10-25','2024-10-25','2025-10-25']),
])
async def test_non_monthly_detected_patterns_are_persisted_and_projected(db,frequency,dates):
    for index,date in enumerate(dates):db.add(tx(f'income{index}',10000,'salary',counterparty='固定收入',when=date+'T04:00:00'))
    await db.commit();await sync(db)
    rows=(await list_recurring(db,'U1'))['items']
    assert len(rows)==1 and rows[0]['frequency']==frequency
    result=await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert any(e['source']=='salary_estimate' and e['reference_id']==rows[0]['stream_id'] for e in result['events'])


@pytest.mark.asyncio
async def test_manual_plan_edit_disable_ownership_and_calendar_boundaries(db):
    created=await schedules.save_plan(db,'U1',plan(frequency='QUARTERLY',anchor_at=datetime(2026,1,31,4,tzinfo=timezone.utc)));await db.commit()
    stream=await db.get(Stream,created['stream_id'])
    assert stream.source=='manual'
    dates=list(schedules.occurrences(datetime(2026,1,31,4,tzinfo=timezone.utc),'QUARTERLY',datetime(2026,2,1,tzinfo=TZ),datetime(2026,8,1,tzinfo=TZ)))
    assert [at.date().isoformat() for at in dates]==['2026-04-30','2026-07-31']
    await foundation.sync_users(db,['U1'],now=NOW);await db.commit()
    assert (await db.get(Stream,stream.stream_id)).status=='ACTIVE'
    with pytest.raises(BankError):await schedules.save_plan(db,'U2',plan(),stream.stream_id)
    await schedules.save_plan(db,'U1',plan(active=False,expected_version=stream.version),stream.stream_id);await db.commit()
    assert not any(e['reference_id']==stream.stream_id for e in (await project_cashflow(db,'U1',CashflowScenario(),now=NOW))['events'])


@pytest.mark.asyncio
async def test_one_off_scenario_does_not_change_baseline_or_ledger(db):
    baseline=await project_cashflow(db,'U1',CashflowScenario(days=7),now=NOW)
    request=CashflowScenario(days=7,one_off_events=[{'at':NOW+timedelta(days=2),'label':'旅行','amount_fen':-20000}])
    changed=await project_cashflow(db,'U1',request,now=NOW)
    assert baseline['baseline_end_fen']==changed['baseline_end_fen']
    assert changed['scenario_end_fen']==changed['baseline_end_fen']-20000
    assert changed['planned_outflow_fen']==0
    assert (await db.get(AccountORM,'A1')).balance_fen==100000
    with pytest.raises(BankError):await forecasts.save(db,'U1',request,now=NOW)
    with pytest.raises(BankError):await project_cashflow(db,'U1',CashflowScenario(days=7,one_off_events=[{'at':NOW-timedelta(days=1),'label':'过去','amount_fen':-1}]),now=NOW)


@pytest.mark.asyncio
async def test_forecast_comparison_uses_saved_scope_and_bounded_actual_intervals(db):
    start=datetime(2026,9,1,tzinfo=TZ)
    saved=await forecasts.save(db,'U1',CashflowScenario(days=7),now=start);await db.commit()
    db.add_all([tx('expense',1000,when='2026-09-02T04:00:00'),tx('income',2000,'salary',when='2026-09-03T04:00:00'),tx('out',300,'transfer_out',counterparty='A3',when='2026-09-04T04:00:00'),tx('internal',9999,'transfer_out',counterparty='A2',when='2026-09-04T04:00:00'),tx('after',99999,when='2026-09-10T04:00:00')]);await db.commit()
    result=(await forecasts.comparisons(db,'U1',now=NOW))['items'][0]
    assert result['snapshot_id']==saved['snapshot_id'] and result['complete']
    assert result['actual_net_flow_fen']==700
    assert result['error_fen']==700-result['predicted_net_flow_fen']
    assert (await forecasts.comparisons(db,'U2',now=NOW))['items']==[]


@pytest.mark.asyncio
async def test_consumption_calibration_requires_three_nonoverlapping_observation_periods(db):
    for index,month in enumerate((1,2,3)):
        start=datetime(2026,month,1,tzinfo=timezone.utc)
        db.add(Snapshot(snapshot_id=f'F-{index}',user_id='U1',account_id='A1',start_at=start,end_at=start+timedelta(days=7),opening_fen=100000,predicted_inflow_fen=0,predicted_outflow_fen=1000,payload={'account_ids':['A1'],'variable_fen':1000,'events':[],'fixed_merchants':[]}))
        for day in (1,2,3,4,5):db.add(tx(f'{month}-{day}',1000,when=f'2026-{month:02}-{day:02}T04:00:00'))
    await db.commit();result=await forecasts.calibration(db,'U1','A1',NOW)
    assert result['applied'] and result['sample_periods']==3 and result['factor']==1.5
    none=await forecasts.calibration(db,'U1',None,NOW)
    assert not none['applied'] and none['factor']==1


@pytest.mark.asyncio
async def test_incremental_sync_only_processes_affected_ledger_and_invalidates_touched_pattern(db):
    await recurring_history(db);await sync(db)
    db.add(tx('unrelated',100,counterparty='新商户'));await db.commit()
    result=await foundation.sync_users(db,['U1'],now=NOW,changed_tx_ids=['unrelated']);await db.commit()
    assert result['U1']['transactions']==1
    salary=next(i for i in (await list_recurring(db,'U1'))['items'] if i['kind']=='income')
    assert salary['status']=='ACTIVE'
    db.add(tx('duplicate-salary',999,'salary',counterparty='雇主',when='2026-09-25T04:00:00'));await db.commit()
    await foundation.sync_users(db,['U1'],now=NOW,changed_tx_ids=['duplicate-salary']);await db.commit()
    assert (await db.get(Stream,salary['stream_id'])).status=='STALE'


@pytest.mark.asyncio
async def test_startup_runs_one_versioned_backfill_then_only_missing_rows(db):
    db.add(tx('historical',100));await db.commit();await sync(db)
    first=await foundation.refresh_all(db,now=NOW);await db.commit()
    assert first['U1']['transactions']==1
    again=await foundation.refresh_all(db,now=NOW);await db.commit()
    assert again['U1']['transactions']==0
    db.add(tx('new-import',100,counterparty='新导入'));await db.commit()
    incremental=await foundation.refresh_all(db,now=NOW);await db.commit()
    assert incremental['U1']['transactions']==1


@pytest.mark.asyncio
async def test_manual_and_detected_plans_cannot_both_be_enabled(db):
    from backend.app.services.bank.bill_recurring import set_enabled
    from backend.app.schemas.bill_foundation import RecurringUpdate
    await recurring_history(db);await sync(db)
    detected=next(i for i in (await list_recurring(db,'U1'))['items'] if i['kind']=='expense')
    await schedules.save_plan(db,'U1',plan(name='房租',amount_fen=30000));await db.commit()
    row=await db.get(Stream,detected['stream_id'])
    with pytest.raises(BankError) as duplicate:await set_enabled(db,'U1',row.stream_id,RecurringUpdate(enabled=True,expected_version=row.version))
    assert duplicate.value.code=='DUPLICATE_RECURRING_PLAN'


@pytest.mark.asyncio
async def test_learning_category_supports_corrections_budget_and_natural_language_query(db):
    from backend.app.services.ai.bills import interpret
    db.add(tx('course',10000,category='other',counterparty='学习课程'));await db.commit();await sync(db)
    assert (await foundation.metadata_views(db,'U1',['course']))['course']['suggested_category']=='education'
    await classifications.correct(db,'U1','course',ClassificationUpdate(category='education',expected_version=0));await db.commit()
    query=interpret('2026年9月学习账单',BillQuery(date='2026-09'),now=NOW)
    assert query.category=='education'
    result=await analyze(db,'U1',query,now=NOW)
    assert result['categories'][0]['label']=='学习' and result['totals']['consumption_fen']==10000
    await budgets.save(db,'U1',BudgetUpdate(month='2026-09',category='education',amount_fen=8000));await db.commit()
    assert (await budgets.summary(db,'U1','2026-09',now=NOW))['items'][0]['status']=='exceeded'
