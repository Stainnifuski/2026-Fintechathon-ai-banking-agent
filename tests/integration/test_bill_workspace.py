"""Workspace hints never grant ownership or mix independent report scopes."""
from dataclasses import replace
import json
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from tests.integration.test_bills import db, tx, NOW
from tests.integration.test_nova_bill_insights import setup, call, prepare
from backend.app.schemas.nova import PageContext
from backend.app.schemas.bill_features import BudgetUpdate, RecurringPlan
from backend.app.services.ai import nova
from backend.app.services.bank import bill_budgets, bill_foundation, bill_schedules, nova_bills, nova_conversations
from backend.app.models.nova_bill_action import NovaBillActionORM


def page(section, **values):
    return PageContext.model_validate({'page':'insights','filters':{'date':'2026-09','account_id':'A2'},
        'bill_workspace':{'section':section,**values}})


@pytest.mark.parametrize('workspace',[
    {'section':'budgets','budget_month':'2026-13'},
    {'section':'overview','budget_month':'2026-09'},
    {'section':'checks','subsection':'cashflow'},
    {'section':'forecast','subsection':'recurring','account_id':'A1'},
    {'section':'organize','subsection':'merchants','selected':{'type':'budget','id':'budget'}},
    {'section':'transactions','selected':{'type':'transaction','id':'x'*33}},
    {'section':'budgets','user_id':'U2'},
])
def test_workspace_schema_rejects_mismatched_or_untrusted_fields(workspace):
    with pytest.raises(ValidationError):PageContext.model_validate({'page':'insights','bill_workspace':workspace})


def test_workspace_only_applies_to_bill_page_and_legacy_selection_must_agree():
    with pytest.raises(ValidationError):PageContext.model_validate({'page':'home','bill_workspace':{'section':'overview'}})
    with pytest.raises(ValidationError):PageContext.model_validate({'page':'insights','selected':{'type':'transaction','id':'old'},
        'bill_workspace':{'section':'transactions','selected':{'type':'transaction','id':'new'}}})


@pytest.mark.asyncio
async def test_budget_month_and_selected_budget_are_requeried_not_taken_as_bank_facts(db):
    ctx=await setup(db)
    first=await bill_budgets.save(db,'U1',BudgetUpdate(month='2026-08',amount_fen=11100))
    await bill_budgets.save(db,'U1',BudgetUpdate(month='2026-08',category='dining',amount_fen=22200))
    foreign=await bill_budgets.save(db,'U2',BudgetUpdate(month='2026-08',amount_fen=99999))
    await db.commit()
    ctx=replace(ctx,page=page('budgets',budget_month='2026-08'))
    result=await nova.execute_tool(call('get_budget_status'),ctx)
    assert result['data']['month']=='2026-08' and len(result['data']['items'])==2
    ctx=replace(ctx,page=page('budgets',budget_month='2026-08',selected={'type':'budget','id':first['budget_id']}))
    result=await nova.execute_tool(call('get_budget_status'),ctx)
    assert [item['amount_fen'] for item in result['data']['items']]==[11100]
    assert len((await nova.execute_tool(call('get_budget_status',{'budget_id':None}),ctx))['data']['items'])==2
    assert len((await nova.execute_tool(call('get_budget_status',{'month':'2026-08'}),ctx))['data']['items'])==2
    ctx=replace(ctx,page=page('budgets',budget_month='2026-08',selected={'type':'budget','id':foreign['budget_id']}))
    assert (await nova.execute_tool(call('get_budget_status'),ctx))['error']=='BUDGET_NOT_FOUND'


@pytest.mark.asyncio
async def test_prediction_account_is_independent_and_cannot_select_another_users_account(db):
    ctx=await setup(db)
    ctx=replace(ctx,page=page('forecast',subsection='cashflow',account_id='A1'))
    result=await nova.execute_tool(call('project_cashflow',{'days':7}),ctx)
    assert result['data']['account_id']=='A1'
    assert (await nova.execute_tool(call('project_cashflow',{'days':7,'account_id':None}),ctx))['data']['account_id'] is None
    ctx=replace(ctx,page=page('forecast',subsection='cashflow',account_id='A3'))
    assert (await nova.execute_tool(call('project_cashflow',{'days':7}),ctx)).get('error')


@pytest.mark.asyncio
async def test_selected_catalog_and_recurring_records_require_owned_server_objects(db):
    ctx=await setup(db)
    db.add(tx('other',200,counterparty='另一商户'));await db.commit()
    await bill_foundation.sync_users(db,['U1','U2'],now=NOW);await db.commit()
    catalog=await bill_foundation.catalog(db,'U1');target=catalog['merchants'][0]['merchant_id']
    ctx=replace(ctx,page=page('organize',subsection='merchants',selected={'type':'merchant','id':target}))
    result=await nova.execute_tool(call('get_bill_catalog',{'section':'merchants','page_size':1}),ctx)
    assert result['data']['total']==1 and result['data']['items'][0]['merchant_id']==target
    assert (await nova.execute_tool(call('get_bill_catalog',{'section':'merchants','object_id':None}),ctx))['data']['total']==2
    assert (await nova.execute_tool(call('get_bill_catalog',{'section':'merchants','name':'另一商户'}),ctx))['data']['items'][0]['name']=='另一商户'
    foreign=(await bill_foundation.catalog(db,'U2'))['merchants'][0]['merchant_id']
    ctx=replace(ctx,page=page('organize',subsection='merchants',selected={'type':'merchant','id':foreign}))
    assert (await nova.execute_tool(call('get_bill_catalog',{'section':'merchants'}),ctx))['error']=='BILL_OBJECT_NOT_FOUND'
    saved=await bill_schedules.save_plan(db,'U1',RecurringPlan(name='房租',account_id='A1',kind='expense',frequency='MONTHLY',amount_fen=10000,anchor_at=NOW))
    await db.commit()
    # No report account filter should be carried into the separate recurring panel.
    ctx=replace(ctx,page=PageContext.model_validate({'page':'insights','bill_workspace':{'section':'forecast','subsection':'recurring','selected':{'type':'recurring','id':saved['stream_id']}}}))
    assert (await nova.execute_tool(call('list_recurring_records'),ctx))['data']['items'][0]['stream_id']==saved['stream_id']
    ctx=replace(ctx,user_id='U2')
    assert (await nova.execute_tool(call('list_recurring_records'),ctx))['error']=='RECURRING_NOT_FOUND'


@pytest.mark.asyncio
async def test_structured_preview_fields_preserve_before_after_and_legacy_cards(db):
    ctx=await setup(db,'把预算设为1000元')
    await bill_budgets.save(db,'U1',BudgetUpdate(month='2026-09',amount_fen=50000));await db.commit()
    response=await prepare(ctx,{'action':'budget','month':'2026-09','amount_fen':100000,'expected_version':1})
    card=response['data']['card'];field=next(f for f in card['fields'] if f['label']=='预算金额')
    assert field=={'label':'预算金额','before':'¥500.00','after':'¥1,000.00'}
    row=await db.get(NovaBillActionORM,card['action_id'])
    payload=nova_bills.unpack(row);payload.pop('fields')
    row.payload_ciphertext=nova_conversations._encrypt(json.dumps(payload));await db.commit()
    old=nova_bills.public(row)
    assert old['fields']==[] and old['lines']==card['lines']
    assert (await nova_bills.decide(db,'U1',ctx.session_id,card['action_id'],'confirm'))['status']=='completed'


@pytest.mark.asyncio
async def test_manual_split_candidates_are_owned_completed_and_paginated(db):
    from backend.app.services.bank import bill_merchants
    from backend.app.services.bank.transfer_service import BankError
    from backend.app.models import TransactionORM
    from sqlalchemy import func
    await setup(db)
    db.add_all([tx(f'book-{index}',100+index,counterparty='书店') for index in range(24)])
    db.add(tx('failed-book',999,counterparty='书店',status='failed'))
    await db.commit();await bill_foundation.sync_users(db,['U1'],now=NOW);await db.commit()
    target=next(row['merchant_id'] for row in (await bill_foundation.catalog(db,'U1'))['merchants'] if row['name']=='书店')
    count=await db.scalar(select(func.count()).select_from(TransactionORM))
    first=await bill_merchants.split_candidates(db,'U1',target,1)
    second=await bill_merchants.split_candidates(db,'U1',target,2)
    assert first['total']==25 and len(first['items'])==20 and first['has_more']
    assert len(second['items'])==5 and not second['has_more']
    ids={row['tx_id'] for row in [*first['items'],*second['items']]}
    assert len(ids)==25 and 'failed-book' not in ids and 'foreign' not in ids
    assert all(row['counterparty']=='书店' and row['status']=='completed' and row['eligible'] for row in first['items'])
    with pytest.raises(BankError):await bill_merchants.split_candidates(db,'U2',target)
    assert await db.scalar(select(func.count()).select_from(TransactionORM))==count
