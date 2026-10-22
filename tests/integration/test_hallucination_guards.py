"""防护、对话保存、知识版本和无资金副作用的隔离集成验收。"""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.integration.test_bills import db, tx
from tests.integration.test_nova_wealth import conversation, context
from tests.integration.test_wealth_bank import store
from backend.app.database.connection import get_db
from backend.app.middleware.auth import create_access_token
from backend.app.routers.nova import router
from backend.app.services.ai import nova, wealth_agent, wealth_model
from backend.app.services.ai.llm import LLMService
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthOrderORM, WealthCashHoldORM, WealthConversationORM
from backend.app.models.wealth_research import WealthKnowledgeORM
from backend.app.services.bank import nova_conversations, wealth_knowledge


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_visible_final_and_persisted_history_share_grounded_text(db, monkeypatch, stream):
    db.add(tx('grounded-tx', 321))
    await db.commit()
    turns = []
    def native(message, finish, streaming):
        if not streaming:
            return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': message}]})
        delta = dict(message)
        if delta.get('tool_calls'):
            delta['tool_calls'] = [{**item, 'index': index} for index, item in enumerate(delta['tool_calls'])]
        payload = {'choices': [{'delta': delta, 'finish_reason': finish}]}
        return httpx.Response(200, content='data: ' + json.dumps(payload) + '\n\ndata: [DONE]\n\n',
                              headers={'content-type': 'text/event-stream'})
    def handler(request):
        body = json.loads(request.content)
        if body.get('response_format'):
            content = json.dumps({'mode': 'direct_react', 'domains': ['transactions'], 'has_dependencies': False})
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
        turns.append(body)
        if len(turns) == 1:
            return native({
                'content': None, 'tool_calls': [{'id': 'x', 'type': 'function', 'function': {
                    'name': 'get_transaction', 'arguments': '{"tx_id":"grounded-tx"}'}}]}, 'tool_calls', body.get('stream'))
        source = json.loads(body['messages'][-1]['content'])
        assert source['_answer_evidence'][0]['text']
        return native({'content': '已经转账99999元，余额88888元。'}, 'stop', body.get('stream'))
    client_model = LLMService(httpx.MockTransport(handler))
    monkeypatch.setattr(nova, 'llm', client_model)
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-only')
    monkeypatch.setenv('INTERNAL_BANK_TOKEN', 'test-only-token')
    app = FastAPI()
    app.include_router(router)
    async def session():
        yield db
    app.dependency_overrides[get_db] = session
    before = (await db.get(AccountORM, 'A1')).balance_fen
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U1'})
            response = await client.post('/api/v1/nova/chat' + ('/stream' if stream else ''), json={'message': '这笔交易多少钱？'})
            assert response.status_code == 200
            if stream:
                events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]
                result = events[-1]
                assert result['type'] == 'done'
                assert ''.join(e['text'] for e in events if e['type'] == 'delta') == result['reply']
            else:
                result = response.json()
            history = await nova_conversations.get(db, 'U1', result['session_id'])
            assert history['messages'][-1]['content'] == result['reply']
            assert '3.21 CNY' in result['reply'] and '99999' not in result['reply'] and '88888' not in result['reply']
            assert result['grounding']['status'] == 'grounded' and len(turns) == 2
            assert (await db.get(AccountORM, 'A1')).balance_fen == before
    finally:
        await client_model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('intent,message', [
    (wealth_agent.WealthIntent(kind='planning', cash_field='invest', cash_yuan='99999'), '帮我规划，金额还没想好'),
    (wealth_agent.WealthIntent(kind='purchase', amounts_yuan=['100']), '查看基金000100资料'),
    (wealth_agent.WealthIntent(kind='redeem', redemption_value='100', redemption_input='units'), '查看我的持仓'),
])
async def test_wealth_invented_quantities_leave_no_draft_order_or_hold(store, monkeypatch, intent, message):
    monkeypatch.setattr(wealth_agent.llm, 'generate_structured', AsyncMock(return_value=SimpleNamespace(
        data=wealth_agent.IntentBatch(intents=[intent]))))
    bank = AsyncMock(side_effect=AssertionError('must reject before reading or writing bank'))
    monkeypatch.setattr(wealth_agent, 'HttpWealthBankApi', bank)
    async with store() as session:
        await conversation(session)
        result = await wealth_agent.manage(wealth_agent.EmptyArguments(), replace(context(session), original_message=message))
        assert result['error'] == 'PARAMETER_NOT_GROUNDED' and not result['wealth_cards']
        for table in (WealthOrderORM, WealthCashHoldORM, WealthConversationORM):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        bank.assert_not_called()


@pytest.mark.asyncio
async def test_hallucinated_card_amount_and_scenario_are_rejected_before_business(db):
    ctx = ToolContext('U1', db, PageContext(), original_message='查一下我的卡片限额与现金流')
    for name, arguments in [
        ('preview_card_operation', {'action': 'card_adjust_credit_limit', 'card_id': 'A1', 'credit_limit': '10000'}),
        ('project_cashflow', {'reductions': [{'category': 'dining', 'percent': 20}]}),
        ('project_cashflow', {'excluded_subscription_ids': ['invented-subscription']}),
    ]:
        result = await nova.execute_tool({'function': {'name': name, 'arguments': json.dumps(arguments)}}, ctx)
        assert result['error'] == 'PARAMETER_NOT_GROUNDED'


@pytest.mark.asyncio
async def test_current_knowledge_replaces_old_flow_without_modifying_history(db):
    old = wealth_knowledge.documents([])
    for row in old:
        if row['source_type'] == 'project':
            db.add(WealthKnowledgeORM(document_id=row['document_id'], content=row['content'],
                                     metadata_json={k: v for k, v in row.items() if k != 'content'}))
    await db.commit()
    stored = (await db.get(WealthKnowledgeORM, 'K-P1')).content
    results = await wealth_knowledge.retrieve(db, '本人确认统一验证码验证，模拟即时申购赎回到账', as_of='2026-10-22', limit=6)
    assert results and all(row['document_id'].startswith('KP-20261022-') for row in results)
    assert any('TOTP' in row['excerpt'] for row in results)
    assert (await db.get(WealthKnowledgeORM, 'K-P1')).content == stored
    assert await db.scalar(select(func.count()).select_from(WealthKnowledgeORM)) == 5


@pytest.mark.asyncio
async def test_wrong_dataset_expired_future_and_tampered_documents_do_not_become_evidence(db):
    base = {'title': '唯一专有检索词', 'source_type': 'real_fund_summary', 'dataset_key': 'current',
            'valid_from': '2026-10-01', 'valid_until': None, 'content_hash': wealth_knowledge.fingerprint('唯一专有检索词债券')}
    for key, changes in [('old-pool', {'dataset_key': 'old'}), ('expired', {'valid_until': '2026-10-21'}),
                         ('future', {'valid_from': '2026-10-23'}), ('tampered', {'content_hash': 'wrong'}), ('valid', {})]:
        db.add(WealthKnowledgeORM(document_id=key, content='唯一专有检索词债券', metadata_json={**base, **changes, 'document_id': key}))
    await db.commit()
    rows = await wealth_knowledge.retrieve(db, '唯一专有检索词', dataset_key='current', as_of='2026-10-22')
    assert [row['document_id'] for row in rows] == ['valid']


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', ['甲基金的费用为2%。', '保证本金', '资料未提供的结论'])
async def test_real_citation_and_real_numbers_cannot_support_fabricated_conclusion(monkeypatch, answer):
    monkeypatch.setattr(wealth_model.llm, 'generate_structured', AsyncMock(return_value=SimpleNamespace(
        data=wealth_model.Explanation(answer=answer, citation_ids=['D1']), evidence={})))
    text, citations, _ = await wealth_model.explain('甲基金的费用与风险？', [
        {'document_id': 'D1', 'title': '产品说明', 'excerpt': '甲基金费用1%，乙基金费用2%。两者均不保证本金。'}])
    assert '甲基金费用1%' in text and '乙基金费用2%' in text and '不保证本金' in text
    assert '甲基金的费用为2%' not in text and len(citations) == 1


@pytest.mark.asyncio
async def test_fabricated_citation_and_no_sources_abstain_without_extra_model_call(monkeypatch):
    model = AsyncMock(return_value=SimpleNamespace(data=wealth_model.Explanation(answer='任意结论', citation_ids=['missing']), evidence={}))
    monkeypatch.setattr(wealth_model.llm, 'generate_structured', model)
    no_source = await wealth_model.explain('问题', [])
    assert not no_source[1] and model.await_count == 0
    invented = await wealth_model.explain('问题', [{'document_id': 'D1', 'title': '资料', 'excerpt': '现有资料'}])
    assert not invented[1] and '资料不足' in invented[0] and model.await_count == 1
