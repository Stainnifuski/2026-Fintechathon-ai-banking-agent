"""Semantic decisions are mocked; no keyword routing, network, or real model calls."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from sqlalchemy import select, func

from backend.app.models.wealth import WealthConversationORM
from backend.app.services.ai import nova, nova_runtime, planning_knowledge as knowledge
from backend.app.services.ai.llm import LLMService
from tests.unit.test_nova_runtime import context

GENERATED = '理财规划可以先梳理资金的流动性与投资安排，帮助你看清各部分的取舍。'

QUOTE = '先梳理整体配置，再考虑具体产品，可以把资金安排中的取舍表达清楚：哪些部分需要保持流动性，哪些部分用于较稳健的投资，哪些部分愿意承担更大的波动。'


class Model:
    def __init__(self, mode, quote=None, *, covered=True, parts=None, calls=None, investment_function='none', action=True):
        self.mode, self.quote, self.covered = mode, quote, covered
        self.parts = parts if parts is not None else [{'text': GENERATED, 'evidence': [{'source': 'knowledge', 'quote': QUOTE}]}]
        self.calls = calls or []
        self.investment_function, self.action = investment_function, action
        self.seen = []

    async def generate_structured(self, **kwargs):
        schema = kwargs['schema']
        self.seen.append(kwargs)
        if schema is nova_runtime.DependencyDecision:
            data = schema(depends_on_prior_task=False, reason='semantic decision',
                          planning_mode=self.mode, start_quote=self.quote,investment_function=self.investment_function,
                          investment_category='mixed_fund' if self.investment_function=='category_recommendation' else None,
                          investment_action_quote=json.loads(kwargs['messages'][1]['content'])['message'] if self.mode=='offer' and self.action else None,
                          unmatched_investment_reason='现有具体功能均不匹配' if self.mode=='offer' else None)
        elif schema is knowledge.KnowledgeAnswer:
            data = schema(covered=self.covered, parts=self.parts)
        elif schema is nova_runtime.ToolSelection:
            data = schema(tool_names=[c.name for c in self.calls])
        elif schema is nova_runtime.ToolPlan:
            data = schema(calls=self.calls)
        else:
            raise AssertionError(schema)
        return SimpleNamespace(data=data)

    async def generate_text(self, **kwargs):
        return SimpleNamespace(data='已打开目标产品推荐。')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['offer', 'explain'])
async def test_explanation_never_exposes_tools_or_opens_cards(context, monkeypatch, mode):
    execute = AsyncMock(side_effect=AssertionError('explanation must not call business or web tools'))
    monkeypatch.setattr(nova, 'execute_tool', execute)
    model = Model(mode)
    result = await nova_runtime.chat('用户的投资需求或模型追问', [], model=model, context=context)
    assert GENERATED in result['reply']
    assert QUOTE not in result['reply']
    assert (knowledge.START_LINK in result['reply']) == (mode == 'offer')
    assert await context.db.scalar(select(func.count()).select_from(WealthConversationORM)) == 0
    assert [x['schema'] for x in model.seen] == [nova_runtime.DependencyDecision, knowledge.KnowledgeAnswer]
    payload = json.loads(model.seen[-1]['messages'][1]['content'])
    assert payload['knowledge'] == knowledge.KNOWLEDGE_PATH.read_text(encoding='utf-8')
    assert payload['phase'] == ('offer' if mode == 'offer' else 'followup')
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_followup_uses_history_and_does_not_modify_existing_card(context):
    row = WealthConversationORM(session_id='NW-existing', user_id='u1', version=1,
        data={'nova_session_id':'test-session', 'command':{'kind':'planning'}, 'closed':False})
    context.db.add(row)
    await context.db.commit()
    history = [{'role':'assistant', 'content':'这里的范围不是收益预测。'}]
    model = Model('explain')
    await nova_runtime.chat('为什么？', history, model=model, context=context)
    assert json.loads(model.seen[-1]['messages'][1]['content'])['recent_turns'] == history
    assert row.version == 1
    assert await context.db.scalar(select(func.count()).select_from(WealthConversationORM)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,quote', [('none', None), ('start', '助手捏造的确认')])
async def test_new_planning_requires_verified_current_turn_confirmation(context, monkeypatch, mode, quote):
    execute = AsyncMock(side_effect=AssertionError('unconfirmed planning must not execute'))
    monkeypatch.setattr(nova, 'execute_tool', execute)
    model = Model(mode, quote, calls=[nova_runtime.PlannedCall(name='plan_wealth', arguments={'kind':'planning'})])
    result = await nova_runtime.chat('我还没决定', [], model=model, context=context)
    assert knowledge.START_LINK not in result['reply']
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['我确认进入理财规划，请开始新的理财规划任务', '好的，开始吧'])
async def test_click_or_natural_confirmation_opens_once_with_grounded_intro(context, monkeypatch, message):
    calls = []
    async def execute(call, ctx):
        calls.append(call)
        ctx.db.add(WealthConversationORM(session_id='NW-confirmed', user_id='u1', version=1,
            data={'nova_session_id':ctx.session_id, 'request_id':ctx.request_id,
                  'command':{'kind':'planning'}, 'closed':False}))
        await ctx.db.commit()
        return {'wealth_cards':[], 'reply':'旧固定说明不应出现'}
    monkeypatch.setattr(nova, 'execute_tool', execute)
    acknowledgment = '已进入规划任务，请在卡片中继续核对。'
    model = Model('start', message, parts=[{'text':acknowledgment,
        'evidence':[{'source':'local','quote':'"phase": "started"'}]}],
        calls=[nova_runtime.PlannedCall(name='plan_wealth', arguments={'kind':'planning'})])
    result = await nova_runtime.chat(message, [{'role':'assistant','content':GENERATED + knowledge.START_LINK}],
                                     model=model, context=replace(context, original_message=message))
    assert len(calls) == 1
    assert result['reply'] == acknowledgment
    assert GENERATED not in result['reply'] and '旧固定说明' not in result['reply']
    payload = json.loads(model.seen[-1]['messages'][1]['content'])
    assert payload['phase'] == 'started'
    assert GENERATED in payload['recent_turns'][0]['content']
    assert knowledge.START_LINK not in result['reply']
    assert await context.db.scalar(select(func.count()).select_from(WealthConversationORM)) == 1


@pytest.mark.asyncio
async def test_specific_recommendation_is_not_replaced_by_planning_offer(context, monkeypatch):
    execute = AsyncMock(return_value={'reply':'已打开推荐卡'})
    monkeypatch.setattr(nova, 'execute_tool', execute)
    model = Model('none', calls=[nova_runtime.PlannedCall(name='plan_wealth',
        arguments={'kind':'recommend', 'category':'bond_fund'})])
    result = await nova_runtime.chat('推荐债券基金', [], model=model, context=context)
    execute.assert_awaited_once()
    assert knowledge.START_LINK not in result['reply']
    assert knowledge.KnowledgeAnswer not in [x['schema'] for x in model.seen]


@pytest.mark.asyncio
async def test_supported_category_blocks_inconsistent_planning_offer(context, monkeypatch):
    execute = AsyncMock(return_value={'reply':'已打开混合基金候选'})
    monkeypatch.setattr(nova,'execute_tool',execute)
    model = Model('offer', investment_function='category_recommendation', calls=[
        nova_runtime.PlannedCall(name='plan_wealth',arguments={'kind':'recommend','category':'mixed_fund'})])
    reply = await nova_runtime.chat('想买混合基金',[],model=model,context=context)
    assert knowledge.START_LINK not in reply['reply']
    assert knowledge.KnowledgeAnswer not in [call['schema'] for call in model.seen]
    execute.assert_awaited_once()
    assert json.loads(execute.call_args.args[0]['function']['arguments'])['category'] == 'mixed_fund'


@pytest.mark.asyncio
async def test_no_confirmed_investment_action_cannot_offer_planning(context, monkeypatch):
    execute=AsyncMock(side_effect=AssertionError('knowledge question must not open a card'))
    monkeypatch.setattr(nova,'execute_tool',execute)
    reply=await nova_runtime.chat('投资是什么意思？',[],model=Model('offer',action=False),context=context)
    assert knowledge.START_LINK not in reply['reply']
    execute.assert_not_awaited()


def test_planning_offer_requires_action_evidence_and_unmatched_capability():
    valid=nova_runtime.DependencyDecision(depends_on_prior_task=False,reason='semantic',planning_mode='offer',
        investment_action_quote='想办理投资',unmatched_investment_reason='已开放投资功能不能承接该操作')
    assert nova_runtime.may_offer_planning(valid,'我想办理投资')
    for update in ({'investment_action_quote':None},{'investment_action_quote':'捏造原文'},
                   {'investment_function':'category_recommendation'},{'unmatched_investment_reason':None}):
        assert not nova_runtime.may_offer_planning(valid.model_copy(update=update),'我想办理投资')


@pytest.mark.asyncio
async def test_planning_intro_preserves_other_business_summary(context, monkeypatch):
    monkeypatch.setattr(nova, 'execute_tool', AsyncMock(return_value={'reply':'已核对'}))
    model = Model('start', '开始规划', calls=[
        nova_runtime.PlannedCall(name='get_financial_overview', arguments={}),
        nova_runtime.PlannedCall(name='plan_wealth', arguments={'kind':'planning'}),
    ])
    result = await nova_runtime.chat('查看资产并开始规划', [], model=model, context=context)
    assert result['reply'].startswith('已打开目标产品推荐。')  # Fake model's business summary.
    assert GENERATED in result['reply']


@pytest.mark.asyncio
async def test_uncovered_and_fabricated_facts_are_not_rendered():
    result = await knowledge.explain(Model('explain', covered=False, parts=[]), '知识库之外的问题', [])
    assert result == knowledge.UNAVAILABLE
    result = await knowledge.explain(Model('explain', parts=[{'text':GENERATED,'evidence':[{'source':'knowledge','quote':QUOTE}]},
        {'text':'这个模型保证每年收益20%','evidence':[{'source':'knowledge','quote':'捏造的知识库依据'}]}]), '说明模型', [])
    assert GENERATED in result and '保证每年收益20%' not in result and knowledge.UNAVAILABLE in result


@pytest.mark.asyncio
async def test_local_evidence_is_scoped_to_supplied_data():
    phrase = '当前规划尚未选定产品'
    model = Model('explain', parts=[{'text':'你当前还没有选择产品。','evidence':[{'source':'local','quote':phrase}]}])
    assert await knowledge.explain(model, '我的进度', [], local_data={'status':phrase}) == '你当前还没有选择产品。'
    assert await knowledge.explain(model, '他人的进度', []) == knowledge.UNAVAILABLE


@pytest.mark.asyncio
async def test_followup_renders_corresponding_generated_principle_and_keeps_evidence_internal():
    quote = '心理账户是资金组织的行为理论，分账户与整体协调是组合研究中的设计思路，凸优化和投影是数学计算方法。'
    text = '心理账户帮助解释不同资金用途；整体协调让各板块仍放在同一份资产结构中核对。'
    model = Model('explain', parts=[{'text':text,'evidence':[{'source':'knowledge','quote':quote}]}])
    reply = await knowledge.explain(model, '心理账户和整体协调各有什么作用？',
        [{'role':'assistant','content':GENERATED}])
    assert reply == text
    assert quote not in reply and GENERATED not in reply
    assert knowledge.START_LINK not in reply
    assert json.loads(model.seen[0]['messages'][1]['content'])['phase'] == 'followup'


@pytest.mark.asyncio
async def test_diagnostics_match_the_actual_model_payload_and_final_prose(monkeypatch):
    events = []
    monkeypatch.setattr(knowledge.debug_trace, 'record', lambda name, value: events.append((name,value)))
    model = Model('offer')
    reply = await knowledge.explain(model, '如何投资', [], offer=True)
    sent = next(value for name,value in events if name == 'planning_knowledge_input')
    saved = next(value for name,value in events if name == 'planning_knowledge_output')
    assert sent['messages'] == model.seen[0]['messages']
    assert saved['reply'] == reply == GENERATED + '\n\n' + knowledge.START_LINK
    assert saved['answer']['parts'][0]['text'] == GENERATED


@pytest.mark.asyncio
async def test_existing_planning_continues_without_restarting_or_appending_intro():
    text = '继续使用原规划卡片，请核对当前选择。'
    model = Model('none', parts=[{'text':text,'evidence':[{'source':'local','quote':'本轮沿用已有理财规划任务。'}]}])
    reply = await knowledge.explain(model, '继续核对', [{'role':'assistant','content':GENERATED}],
        intro=True, continuing=True, local_data={'wealth_cards':[]})
    assert reply == text
    payload = json.loads(model.seen[0]['messages'][1]['content'])
    assert payload['phase'] == 'active'
    assert '已确认启动理财规划' not in payload['authorized_local_data_text']


@pytest.mark.asyncio
async def test_actual_deepseek_http_payload_has_full_knowledge_and_generated_text_is_final(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'isolated-test-key')
    monkeypatch.setenv('LLM_MODE', 'model')
    captured = []
    answer = {'covered':True, 'parts':[{'text':GENERATED,
        'evidence':[{'source':'knowledge','quote':QUOTE}]}]}
    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(answer,ensure_ascii=False)},
                                                     'finish_reason':'stop'}]})
    client = LLMService(httpx.MockTransport(handler))
    try:
        reply = await knowledge.explain(client, '如何投资', [], offer=True)
    finally:
        await client.aclose()
    body = captured[0]
    payload = json.loads(next(m['content'] for m in body['messages'] if m['role'] == 'user'))
    assert payload['knowledge'] == knowledge.KNOWLEDGE_PATH.read_text(encoding='utf-8')
    assert payload['question'] == '如何投资' and payload['phase'] == 'offer'
    assert body['response_format'] == {'type':'json_object'}
    assert 'tools' not in body
    assert reply == GENERATED + '\n\n' + knowledge.START_LINK
    assert QUOTE not in reply
