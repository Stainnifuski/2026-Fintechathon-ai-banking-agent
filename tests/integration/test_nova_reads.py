"""NOVA 业务闭环：共用口径、身份隔离、页面继承与只读验证。"""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.integration.test_bills import db, tx, QUERY
from backend.app.database.connection import get_db
from backend.app.middleware.auth import create_access_token
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth import WealthCashHoldORM
from backend.app.models.wealth_research import WealthContextORM, WealthDatasetORM
from backend.app.routers.nova import router
from backend.app.schemas.nova import PageContext, BillFilters, Selection, BillDetailContext
from backend.app.services.ai import nova
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.ai.llm import LLMService
from backend.app.services.bank import bill_service, data_service
from backend.app.schemas.bills import BillAnalysis


def call(name, arguments=None):
    return {'function': {'name': name, 'arguments': json.dumps(arguments or {})}}


def context(db, **kwargs):
    return ToolContext('U1', db, PageContext(page='insights', filters=BillFilters(date='2026-09'), **kwargs))


@pytest.mark.asyncio
async def test_overview_matches_accounts_and_excludes_other_users_contexts_credit(db):
    db.add(AccountORM(account_id='C1', user_id='U1', account_type='credit', card_number='6222000012345678', balance_fen=990000))
    db.add(WealthDatasetORM(dataset_key='test', dataset_id='test', version='1', mode='synthetic', manifest={}, ingested_at=datetime.now(timezone.utc)))
    await db.flush()
    db.add(WealthContextORM(context_id='test', user_id='U1', account_id='A2', dataset_key='test', data={}))
    # Fixture 的内存 SQLite 不启用 FK；只读占款口径测试不需要构造订单状态机。
    db.add(WealthCashHoldORM(operation_id='test-hold', account_id='A1', amount_fen=2222, status='HELD'))
    await db.commit()
    before = (await db.get(AccountORM, 'A1')).balance_fen
    result = await nova.execute_tool(call('get_financial_overview'), context(db))
    page = await data_service.list_accounts('U1', session=db)
    cash = [row for row in page if row['account_type'] == 'savings']
    assert result['data']['totals'] == {'balance_fen': 100000, 'available_fen': 97778, 'held_fen': 2222}
    assert result['data']['totals']['available_fen'] == sum(row['available_fen'] for row in cash)
    assert {row['account_id'] for row in result['data']['accounts']} == {'A1', 'C1'}
    assert '6222000012345678' not in json.dumps(result)
    assert 'user_id' not in json.dumps(result) and 'password' not in json.dumps(result)
    assert (await db.get(AccountORM, 'A1')).balance_fen == before
    assert result['data_kind'] == 'simulated_bank' and result['amount_unit'] == 'fen'


@pytest.mark.asyncio
async def test_analysis_matches_page_and_aggregates_beyond_pagination(db):
    db.add_all([tx(f'T{i:04}', 1) for i in range(61)] + [tx('other-user', 99999, account='A3')])
    await db.commit()
    result = await nova.execute_tool(call('get_bill_analysis'), context(db))
    page = BillAnalysis.model_validate(await bill_service.analyze(db, 'U1', QUERY)).model_dump(mode='json')
    assert result['data']['totals'] == page['totals']
    assert result['data']['categories'] == page['categories']
    assert result['data']['trend'] == page['trend']
    assert result['data']['totals']['consumption_fen'] == 61
    listing = await nova.execute_tool(call('list_transactions', {'page_size': 10}), context(db))
    assert listing['data']['total'] == 61 and len(listing['data']['items']) == 10
    assert listing['data']['has_more'] is True and listing['data']['query']['date'] == '2026-09'
    assert await db.scalar(select(func.count()).select_from(TransactionORM)) == 62


@pytest.mark.asyncio
async def test_filters_selection_and_ownership(db):
    db.add_all([tx('own', 321), tx('shop', 100, category='shopping'), tx('foreign', 900, account='A3'),
                tx('pending', 123, status='pending')])
    await db.commit()
    ctx = context(db, selected=Selection(type='transaction', id='own'),
                  details=BillDetailContext(category='dining', tx_ids=['own']))
    detail = await nova.execute_tool(call('get_transaction'), ctx)
    assert detail['data']['tx_id'] == 'own' and detail['data']['amount_fen'] == 321
    listing = await nova.execute_tool(call('list_transactions'), ctx)
    assert [row['tx_id'] for row in listing['data']['items']] == ['own']
    listing = await nova.execute_tool(call('list_transactions', {'category': None, 'tx_ids': []}), ctx)
    assert listing['data']['total'] == 2
    for key in ('foreign', 'missing', 'pending'):
        result = await nova.execute_tool(call('get_transaction', {'tx_id': key}), ctx)
        assert result['error'] == 'TRANSACTION_NOT_FOUND'
    ctx_foreign = context(db, selected=Selection(type='transaction', id='foreign'))
    assert (await nova.execute_tool(call('get_transaction'), ctx_foreign))['error'] == 'TRANSACTION_NOT_FOUND'
    assert (await nova.execute_tool(call('get_bill_analysis', {'account_id': 'A3'}), ctx))['error'] == 'ACCOUNT_NOT_FOUND'
    assert (await nova.execute_tool(call('get_transaction'), context(db)))['error'] == 'MISSING_SELECTION'
    assert (await nova.execute_tool(call('get_financial_overview')))['error'] == 'AUTH_CONTEXT_REQUIRED'
    for name in ('get_financial_overview', 'get_bill_analysis', 'list_transactions', 'get_transaction'):
        assert (await nova.execute_tool(call(name, {'user_id': 'U2'}), ctx))['error'] == 'INVALID_ARGUMENTS'
    for arguments in ({'page_size': 51}, {'page': 0}, {'page_size': '10'}, {'category': 'invalid'}, {'tx_ids': ['x'] * 11}):
        assert (await nova.execute_tool(call('list_transactions', arguments), ctx))['error'] == 'INVALID_ARGUMENTS'
    assert (await nova.execute_tool(call('get_bill_analysis', {'date': 'bad'}), ctx))['error'] == 'INVALID_BILL_PERIOD'


@pytest.mark.asyncio
async def test_offline_business_same_tools(db):
    db.add(tx('own', 321))
    await db.commit()
    ctx = context(db, selected=Selection(type='transaction', id='own'))
    for message, name in [('我现在有多少钱可以用？', 'get_financial_overview'),
                          ('解释当前筛选的账单', 'get_bill_analysis'),
                          ('这笔流水是什么？', 'get_transaction'), ('查看流水明细', 'list_transactions')]:
        result = await nova.chat(message, [], context=ctx)
        assert result['mode'] == 'offline' and result['tool_calls'][0]['name'] == name
        assert '模拟银行' in result['reply']
    assert not (await nova.chat('给别人转账并查询余额', [], context=ctx))['tool_calls']


@pytest.mark.asyncio
async def test_authenticated_api_native_business_loop_and_context_validation(db, monkeypatch):
    db.add(tx('own', 321))
    await db.commit()
    seen = []
    def handler(request):
        body = json.loads(request.content)
        if body.get('response_format'):
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps({'mode': 'direct_react', 'domains': ['transactions'],
                                      'has_dependencies': False})}}]})
        seen.append(body)
        if len(seen) == 1:
            assert 'user_id' not in json.dumps(body)
            page_message = next(m for m in body['messages'] if m['content'].startswith('当前页面提示'))
            assert 'own' in page_message['content'] and '2026-09' in page_message['content']
            return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
                'content': None, 'tool_calls': [{'id': 'selected', 'type': 'function', 'function': {
                    'name': 'get_transaction', 'arguments': '{}'}}]}}]})
        tool = body['messages'][-1]
        assert tool['role'] == 'tool' and tool['tool_call_id'] == 'selected'
        assert json.loads(tool['content'])['data']['amount_fen'] == 321
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '模拟银行这笔消费为3.21元'}}]})
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    model = LLMService(httpx.MockTransport(handler))
    monkeypatch.setattr(nova, 'llm', model)
    app = FastAPI()
    app.include_router(router)
    async def request_db():
        yield db
    app.dependency_overrides[get_db] = request_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U1'})
            payload = {'message': '这笔多少钱？', 'page_context': {'version': 1, 'page': 'insights',
                'filters': {'date': '2026-09'}, 'selected': {'type': 'transaction', 'id': 'own'},
                'observed_as_of': '2026-10-01T00:00:00+08:00'}}
            response = await client.post('/api/v1/nova/chat', json=payload)
            assert response.status_code == 200 and '3.21 CNY' in response.json()['reply']
            assert response.json()['grounding']['status'] == 'grounded'
            assert len(seen) == 2
            invalid = [
                {'page': 'home', 'user_id': 'U2'}, {'version': 2, 'page': 'home'},
                {'page': 'home', 'filters': {'date': '2026-09'}},
                {'page': 'insights', 'observed_as_of': '2026-10-01T00:00:00'},
                {'page': 'insights', 'filters': {'balance': 100000}},
                {'page': 'insights', 'details': {'page': 0}},
            ]
            for value in invalid:
                assert (await client.post('/api/v1/nova/chat', json={'message': '查询', 'page_context': value})).status_code == 422
    finally:
        await model.aclose()
