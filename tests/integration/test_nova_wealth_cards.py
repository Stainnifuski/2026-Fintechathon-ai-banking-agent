from types import SimpleNamespace
from dataclasses import replace
import json
from unittest.mock import AsyncMock
from datetime import datetime,timedelta,timezone

import pytest
from sqlalchemy import select,func

from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import conversation,context
from backend.app.services.ai import wealth_agent as agent
from backend.app.services.bank import nova_conversations as chats
from backend.app.models.wealth import WealthConversationORM,WealthOrderORM,WealthCashHoldORM
from backend.app.models.wealth_research import WealthChatRequestORM
from backend.app.models.nova_conversation import NovaConversationORM


def fake_model(monkeypatch,intent):
    monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[intent]))))
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={
        'context_id':'test','cash':[{'available_fen':123456,'account_type':'savings'}],
        'asset_summary':{'positions_market_value_fen':0,'total_assets_fen':123456}})))


@pytest.mark.asyncio
async def test_closed_card_does_not_block_new_wealth_request(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='planning',task_id='NW-closed'))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-closed',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','request_id':'old-request','context_id':'test',
            'command':{'kind':'recommend','products':[]},'closed':True}))
        await db.commit()
        ctx=replace(context(db),wealth_task_id='NW-closed',wealth_stage='match',original_message='我要理财')
        result=await agent.manage(agent.EmptyArguments(),ctx)
        cards=result['wealth_cards']
        assert len(cards)==2
        assert any(card['task_id']=='NW-closed' and card['closed'] for card in cards)
        assert any(card['command']['kind']=='planning' and not card['closed'] for card in cards)
        prompt=json.loads(agent.llm.generate_structured.call_args.kwargs['messages'][1]['content'])
        assert prompt['focused_card']=={'task_id':None,'stage':None}
        assert prompt['tasks']==[]


@pytest.mark.asyncio
async def test_broad_wealth_intent_opens_planning_card(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='planning'))
    async with store() as db:
        await conversation(db)
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),original_message='想开始做理财'))
        assert len(result['wealth_cards'])==1
        assert result['wealth_cards'][0]['command']['kind']=='planning'
        system=agent.llm.generate_structured.call_args.kwargs['messages'][0]['content']
        assert '整体理财规划（kind=planning）' in system
        assert '不因这些字段缺失而输出clarify' in system


@pytest.mark.asyncio
async def test_risk_tool_opens_form_and_accepts_grounded_partial_answer(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='assessment'))
    from backend.app.integrations import wealth_authorization as policy
    monkeypatch.setattr(policy,'latest_assessment',AsyncMock(return_value=None))
    async with store() as db:
        await conversation(db)
        opened=await agent.manage_risk(agent.WealthRiskArguments(),context(db))
        card=opened['wealth_cards'][0]
        assert card['command']['kind']=='assessment' and card['assessment_pending']
        answer=agent.WealthRiskArguments(action='answer',task_id=card['task_id'],answers={'payment':2},
            answer_evidence={'payment':agent.RiskAnswerEvidence(
                question_quote='财务支付能力',option_quote='稳定结余')})
        continued=await agent.manage_risk(answer,replace(context(db,'risk-answer'),
            original_message='财务支付能力选稳定结余'))
        assert continued['wealth_cards'][0]['view_context']['risk_answers']=={'payment':2}
        with pytest.raises(agent.WealthPolicyError):
            await agent.manage_risk(answer,replace(context(db,'risk-invalid'),original_message='其他内容'))


@pytest.mark.asyncio
async def test_main_model_planning_arguments_open_card_without_submodel(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='clarify'))
    agent.llm.generate_structured.side_effect=AssertionError('理财工具不应调用子模型')
    async with store() as db:
        await conversation(db)
        result=await agent.manage(agent.WealthPlanningArguments(kind='planning'),
            replace(context(db),original_message='想做理财规划'))
        assert result['wealth_cards'][0]['command']['kind']=='planning'
        agent.llm.generate_structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_risk_tool_shows_level_then_opens_reassessment(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='assessment'))
    from backend.app.integrations import wealth_authorization as policy
    valid=SimpleNamespace(confirmed_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc)+timedelta(days=20),result={'capacity_level':3})
    monkeypatch.setattr(policy,'latest_assessment',AsyncMock(return_value=valid))
    async with store() as db:
        await conversation(db)
        opened=await agent.manage_risk(agent.WealthRiskArguments(),context(db))
        card=opened['wealth_cards'][0]
        assert 'R3' in opened['reply'] and card['command']['kind']=='assessment'
        assert not card['reassess_open'] and not card['assessment_pending']
        changed=await agent.manage_risk(agent.WealthRiskArguments(action='reassess',task_id=card['task_id']),
            replace(context(db,'risk-reassess'),original_message='重新评估风险'))
        assert changed['wealth_cards'][0]['reassess_open']
        assert changed['wealth_cards'][0]['assessment_pending']
        from backend.app.services.ai.execution_queue import wealth_state
        assert await wealth_state(context(db),card['task_id'],'settled')=='waiting'
        valid.confirmed_at=datetime.now(timezone.utc)+timedelta(seconds=5)
        assert await wealth_state(context(db),card['task_id'],'settled')=='completed'


@pytest.mark.asyncio
async def test_risk_answers_can_be_submitted_and_confirmed_from_explicit_chat(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='assessment'))
    from backend.app.domain.wealth_risk import QUESTIONS
    answers={key:2 for key,_,_ in QUESTIONS}
    clauses=[f'{title}：{options[2]}' for _,title,options in QUESTIONS]
    message='；'.join(clauses)+'；我是成年人，具有完全民事行为能力。'
    evidence={key:agent.RiskAnswerEvidence(question_quote=title,option_quote=options[2])
              for key,title,options in QUESTIONS}
    async with store() as db:
        await conversation(db)
        submitted=await agent.manage_risk(agent.WealthRiskArguments(action='submit',answers=answers,
            answer_evidence=evidence,adult_quote='我是成年人，具有完全民事行为能力'),
            replace(context(db,'risk-submit'),original_message=message))
        assert '请由本人确认' in submitted['reply']
        assert submitted['wealth_cards'][0]['assessment_pending']
        confirmed=await agent.manage_risk(agent.WealthRiskArguments(action='confirm',
            task_id=submitted['wealth_cards'][0]['task_id'],confirm_quote='确认本次风险评估结果'),
            replace(context(db,'risk-confirm'),original_message='确认本次风险评估结果'))
        assert '已确认风险评估等级' in confirmed['reply']
        assert not confirmed['wealth_cards'][0]['assessment_pending']


@pytest.mark.asyncio
async def test_main_model_can_advance_existing_form_with_grounded_free_phrase(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='planning'))
    agent.llm.generate_structured.side_effect=AssertionError('理财子模型不得运行')
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-advance',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','request_id':'old','context_id':'test',
            'command':{'kind':'planning','products':[]},'closed':False}))
        await db.commit()
        args=agent.WealthPlanningArguments(kind='planning',task_id='NW-advance',
            advance='continue',advance_quote='接着核对')
        result=await agent.manage(args,replace(context(db,'new-advance'),
            wealth_task_id='NW-advance',wealth_stage='selected',original_message='我们接着核对选择'))
        assert result['wealth_cards'][0]['instruction']['stage']=='selected'
        assert result['wealth_cards'][0]['instruction']['action']=='continue'


@pytest.mark.asyncio
async def test_assets_and_valid_assessment_are_text_without_card(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='assets'))
    async with store() as db:
        await conversation(db)
        result=await agent.manage(agent.EmptyArguments(),context(db))
        assert '1,234.56' in result['reply'] and not result['wealth_cards']
        from backend.app.integrations import wealth_authorization as policy
        monkeypatch.setattr(policy,'latest_assessment',AsyncMock(return_value=SimpleNamespace(
            confirmed_at=datetime.now(timezone.utc),expires_at=datetime.now(timezone.utc)+timedelta(days=1),result={'capacity_level':3})))
        fake_model(monkeypatch,agent.WealthIntent(kind='assessment'))
        result=await agent.manage(agent.EmptyArguments(),context(db,'request-assessment'))
        assert 'R3' in result['reply'] and not result['wealth_cards']


@pytest.mark.asyncio
@pytest.mark.parametrize('model_action',['confirm','continue','prepare'])
async def test_natural_next_keeps_draft_but_cannot_confirm(store,monkeypatch,model_action):
    fake_model(monkeypatch,agent.WealthIntent(kind='purchase',advance='prepare',advance_quote='下一步'))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-focused',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','command':{'kind':'purchase'},'closed':False}))
        await db.commit()
        ctx=replace(context(db),wealth_task_id='NW-focused',wealth_stage='purchase_review',original_message='下一步')
        result=await agent.manage(agent.EmptyArguments(),ctx)
        assert result['wealth_cards'][0]['instruction']['action']=='prepare'
        assert result['wealth_cards'][0]['command']['kind']=='purchase'
        fake_model(monkeypatch,agent.WealthIntent(kind='purchase',advance=model_action))
        result=await agent.manage(agent.EmptyArguments(),replace(ctx,request_id='request-no-auto-consent',original_message='确认',wealth_stage='trade'))
        assert '手动' in result['reply']
        assert result['wealth_cards'][0]['instruction']['id']!= 'request-no-auto-consent'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0


@pytest.mark.asyncio
async def test_focused_single_amount_keeps_selection_and_updates_saved_value(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='recommend',task_id='NW-amount',amounts_yuan=['100']))
    async with store() as db:
        await conversation(db)
        product={'product_id':'B017','product_version':1,'name':'测试活钱'}
        db.add(WealthConversationORM(session_id='NW-amount',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','closed':False,
            'command':{'kind':'recommend','category':'quick_redeem','products':[product]},
            'view_context':{'selected_products':[product],'amounts_yuan':{'B017':''}}}))
        await db.commit()
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),
            wealth_task_id='NW-amount',wealth_stage='amounts',original_message='这只产品申购金额填100元，先不提交。'))
        card=result['wealth_cards'][0]
        assert card['command']['products']==[product]
        assert card['command']['amounts_yuan']==['100']
        assert card['view_context']['amounts_yuan']=={'B017':'100'}
        assert not card.get('execution_plan_id')
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0


@pytest.mark.asyncio
async def test_delete_is_owner_scoped_purges_linked_cache_and_preserves_bank_facts(store):
    async with store() as db:
        await conversation(db)
        db.add_all([
            WealthConversationORM(session_id='NW-delete',user_id='U1',version=1,data={'nova_session_id':'NOVA-test-session','action_session_ids':['WC-delete']}),
            WealthConversationORM(session_id='WC-delete',user_id='U1',version=1,data={'structured_actions':True}),
            WealthConversationORM(session_id='WC-keep',user_id='U1',version=1,data={'structured_actions':True}),
            WealthChatRequestORM(request_key='NWREQ-delete',user_id='U1',digest='x',status='COMPLETED',response={'session_id':'NOVA-test-session'},started_at=datetime.now(timezone.utc)),
        ])
        await db.commit()
        with pytest.raises(chats.ConversationError):await chats.remove(db,'U2','NOVA-test-session')
        await db.rollback()
        await chats.remove(db,'U1','NOVA-test-session')
        assert await db.get(NovaConversationORM,'NOVA-test-session') is None
        assert await db.get(WealthConversationORM,'NW-delete') is None
        assert await db.get(WealthConversationORM,'WC-delete') is None
        assert await db.get(WealthChatRequestORM,'NWREQ-delete') is None
        assert await db.get(WealthConversationORM,'WC-keep') is not None
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        with pytest.raises(Exception):await agent.manage(agent.EmptyArguments(),context(db))
