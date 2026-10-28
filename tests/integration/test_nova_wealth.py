"""NOVA bridge isolation, replay, ownership and formal trade guard regressions."""
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select, func

from backend.app.services.ai import wealth_agent as agent
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from backend.app.routers.nova import WealthCardState
from backend.app.models.nova_conversation import NovaConversationORM
from backend.app.models.wealth import WealthConversationORM, WealthOrderORM, WealthCashHoldORM, WealthPlanORM, WealthAssessmentORM
from backend.app.integrations.wealth_authorization import WealthPolicyError
from tests.integration.test_wealth_bank import store, http_store
from tests.integration.test_wealth_target_purchase import selected, confirm_verify


def add_assessment(db):
    now=datetime.now(timezone.utc)
    db.add(WealthAssessmentORM(assessment_id='WA-test-current',user_id='U1',profile_version=1,
        questionnaire_version='test',score_version='test',answers={},
        result={'capacity_level':3,'purchase_allowed':True},created_at=now,
        expires_at=now+timedelta(days=1),confirmed_at=now))


async def conversation(db, assessed=True):
    db.add(NovaConversationORM(conversation_id='NOVA-test-session',user_id='U1',title_ciphertext='unused'))
    if assessed:add_assessment(db)
    await db.commit()


def context(db, request='request-wealth-one'):
    return ToolContext('U1',db,PageContext(page='wealth'),original_message='推荐债券基金',
                       request_id=request,session_id='NOVA-test-session')


@pytest.mark.asyncio
@pytest.mark.parametrize('assessment_state',['missing','unconfirmed','expired'])
async def test_missing_assessment_uses_gate_prompt_and_opens_assessment_first(store,monkeypatch,assessment_state):
    calls=[]
    async def model(**kwargs):
        calls.append(kwargs)
        if kwargs['schema'] is agent.AssessmentGateIntent:
            return SimpleNamespace(data=agent.AssessmentGateIntent(kind='planning'))
        return SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='planning')]))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'})))
    async with store() as db:
        await conversation(db,assessed=False)
        if assessment_state!='missing':
            now=datetime.now(timezone.utc)
            db.add(WealthAssessmentORM(assessment_id='WA-incomplete',user_id='U1',profile_version=1,
                questionnaire_version='test',score_version='test',answers={},
                result={'capacity_level':3,'purchase_allowed':True},created_at=now-timedelta(days=2),
                expires_at=now-timedelta(days=1) if assessment_state=='expired' else now+timedelta(days=1),
                confirmed_at=now-timedelta(days=2) if assessment_state=='expired' else None))
            await db.commit()
        ctx=replace(context(db),original_message='我要理财')
        first=await agent.manage(agent.EmptyArguments(),ctx)
        assert '先完成并本人确认风险评估' in first['reply']
        assert first['trusted_reply']==first['reply']
        assert '三板块资产配置模型' not in first['reply']
        assert first['wealth_cards'][0]['command']['kind']=='planning'
        assert first['wealth_cards'][0]['command']['products']==[]
        assert first['wealth_cards'][0]['assessment_pending']
        assert calls[0]['schema'] is agent.AssessmentGateIntent
        assert '当前本人尚无已确认且有效的风险评估' in calls[0]['messages'][0]['content']
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        repeated=await agent.manage(agent.EmptyArguments(),ctx)
        assert repeated['wealth_cards']==first['wealth_cards'] and len(calls)==1
        assert repeated['trusted_reply']==first['reply']
        now=datetime.now(timezone.utc)
        db.add(WealthAssessmentORM(assessment_id='WA-after-gate',user_id='U1',profile_version=1,
            questionnaire_version='test',score_version='test',answers={},
            result={'capacity_level':3,'purchase_allowed':True},created_at=now,
            expires_at=now+timedelta(days=1),confirmed_at=now))
        await db.commit()
        await agent.manage(agent.EmptyArguments(),replace(ctx,request_id='request-after-assessment'))
        assert calls[-1]['schema'] is agent.IntentBatch


@pytest.mark.asyncio
async def test_planning_intro_is_trusted_and_replayed(store,monkeypatch):
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='planning')])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'})))
    async with store() as db:
        await conversation(db)
        ctx=replace(context(db),original_message='帮我做理财规划')
        first=await agent.manage(agent.EmptyArguments(),ctx)
        assert first['wealth_cards'][0]['command']['kind']=='planning'
        assert first['trusted_reply']==first['reply']==agent.PLANNING_MODEL_INTRO
        assert '三板块资产配置模型' in first['reply']
        assert '交易仍需本人确认和验证' in first['reply']
        repeated=await agent.manage(agent.EmptyArguments(),ctx)
        assert repeated['trusted_reply']==first['trusted_reply']
        assert model.await_count==1


@pytest.mark.asyncio
async def test_main_model_tools_open_planning_positions_and_returns_without_submodel(store,monkeypatch):
    model=AsyncMock(side_effect=AssertionError('理财工具不得调用子模型'))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test','cash':[], 'asset_summary':{}}),
        call=AsyncMock(return_value={'performance':{'amount_fen':0,'rate_percent':'0','notice':'已核对'}})))
    async with store() as db:
        await conversation(db)
        calls=[(agent.WealthPlanningArguments(kind='planning'),'request-direct-plan'),
               (agent.WealthPositionsArguments(kind='positions'),'request-direct-positions'),
               (agent.WealthPositionsArguments(kind='redeem'),'request-direct-redeem'),
               (agent.WealthReturnsArguments(period='month'),'request-direct-returns')]
        for arguments,request_id in calls:
            response=await agent.manage(arguments,replace(context(db,request_id),original_message='理财操作'))
            assert any(card['command']['kind']==arguments.kind for card in response['wealth_cards'])
        returns=next(card for card in response['wealth_cards'] if card['command']['kind']=='returns')
        changed=await agent.manage(agent.WealthReturnsArguments(task_id=returns['task_id'],period='year'),
            replace(context(db,'request-direct-returns-update'),original_message='查看近一年的整体理财收益'))
        assert len(changed['wealth_cards'])==len(response['wealth_cards'])
        assert next(card for card in changed['wealth_cards'] if card['task_id']==returns['task_id'])['command']['period']=='year'
        model.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_model_planning_opens_risk_form_without_submodel_when_unassessed(store,monkeypatch):
    model=AsyncMock(side_effect=AssertionError('理财工具不得调用子模型'))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test'})))
    async with store() as db:
        await conversation(db,assessed=False)
        result=await agent.manage(agent.WealthPlanningArguments(kind='planning'),
            replace(context(db),original_message='开始做理财规划'))
        assert result['wealth_cards'][0]['assessment_pending']
        assert result['wealth_cards'][0]['command']['kind']=='planning'
        model.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_risk_answer_updates_pending_planning_card(store,monkeypatch):
    monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(side_effect=AssertionError('不应调用子模型')))
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test'})))
    async with store() as db:
        await conversation(db,assessed=False)
        opened=await agent.manage(agent.WealthPlanningArguments(),replace(context(db),original_message='开始理财'))
        card=opened['wealth_cards'][0]
        answer=agent.WealthRiskArguments(action='answer',task_id=card['task_id'],answers={'payment':2},
            answer_evidence={'payment':agent.RiskAnswerEvidence(
                question_quote='财务支付能力',option_quote='稳定结余')})
        updated=await agent.manage_risk(answer,replace(context(db,'answer-pending'),
            wealth_task_id=card['task_id'],original_message='财务支付能力选稳定结余'))
        assert len(updated['wealth_cards'])==1
        assert updated['wealth_cards'][0]['command']['kind']=='planning'
        assert updated['wealth_cards'][0]['view_context']['risk_answers']=={'payment':2}


@pytest.mark.asyncio
async def test_main_model_can_select_visible_planning_product_and_fill_budget(store,monkeypatch):
    monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(side_effect=AssertionError('不应调用子模型')))
    product={'product_id':'B017','product_version':1,'name':'测试活钱','bucket':'cash_management'}
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test','cash':[], 'asset_summary':{}}),
        call=AsyncMock(return_value=[{'product_id':'B017','version':1,'name':'测试活钱'}])))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-direct-plan',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','request_id':'prior','context_id':'test','closed':False,
            'command':{'kind':'planning','products':[],'cash_yuan':None,'cash_field':None},
            'view_context':{'visible_products':[product],'selected_products':[],'completed_stages':[]}}))
        await db.commit()
        picked=await agent.manage(agent.WealthPlanningArguments(kind='planning',task_id='NW-direct-plan',
            candidate_indices=[1],selection_operation='add'),replace(context(db,'pick'),
            original_message='选第一个产品'))
        card=next(c for c in picked['wealth_cards'] if c['task_id']=='NW-direct-plan')
        assert card['command']['products'][0]['product_id']=='B017'
        budget=await agent.manage(agent.WealthPlanningArguments(kind='planning',task_id='NW-direct-plan',
            cash_field='invest',cash_yuan='1000',cash_quote='本次投资1000元'),
            replace(context(db,'budget'),original_message='本次投资1000元'))
        card=next(c for c in budget['wealth_cards'] if c['task_id']=='NW-direct-plan')
        assert card['command']['cash_yuan']=='1000' and card['command']['products'][0]['product_id']=='B017'
        advanced=await agent.manage(agent.WealthPlanningArguments(kind='planning',task_id='NW-direct-plan',
            candidate_indices=[1],selection_operation='add',advance='continue',
            advance_quote='继续核对'),replace(context(db,'pick-and-continue'),
            wealth_task_id='NW-direct-plan',wealth_stage='cash_management',
            original_message='选第一个产品并继续核对'))
        card=next(c for c in advanced['wealth_cards'] if c['task_id']=='NW-direct-plan')
        assert card['command']['products'][0]['product_id']=='B017'
        assert card['instruction']['action']=='continue'


@pytest.mark.asyncio
async def test_main_model_can_prefill_redeem_from_visible_holding(store,monkeypatch):
    monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(side_effect=AssertionError('不应调用子模型')))
    product={'product_id':'B017','product_version':1,'name':'测试活钱'}
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test','cash':[], 'asset_summary':{}}),
        call=AsyncMock(return_value=[{'product_id':'B017','version':1,'name':'测试活钱'}])))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-direct-holdings',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','request_id':'prior','context_id':'test','closed':False,
            'command':{'kind':'positions','products':[]},
            'view_context':{'selection_kind':'positions','visible_products':[product],
                            'selected_products':[],'completed_stages':[]}}))
        await db.commit()
        result=await agent.manage(agent.WealthPositionsArguments(kind='redeem',task_id='NW-direct-holdings',
            candidate_indices=[1],redemption_input='market_value',redemption_value='100',
            redemption_quote='赎回100元'),replace(context(db,'redeem'),
            wealth_task_id='NW-direct-holdings',wealth_stage='redemption_input',
            original_message='赎回第一个持仓，赎回100元'))
        card=next(c for c in result['wealth_cards'] if c['task_id']=='NW-direct-holdings')
        assert card['command']['kind']=='redeem'
        assert card['command']['products'][0]['product_id']=='B017'
        assert card['command']['redemption_value']=='100'


@pytest.mark.asyncio
async def test_intent_replay_and_no_financial_effect(store, monkeypatch):
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='recommend',category='bond_fund')])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'})))
    async with store() as db:
        await conversation(db)
        first=await agent.manage(agent.EmptyArguments(),context(db))
        second=await agent.manage(agent.EmptyArguments(),context(db))
        assert first['wealth_cards']==second['wealth_cards']
        assert model.await_count==1
        assert first['wealth_cards'][0]['command']['category']=='bond_fund'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        with pytest.raises(WealthPolicyError):await agent.cards(db,'U2','NOVA-test-session')
        changed=context(db)
        from dataclasses import replace
        with pytest.raises(WealthPolicyError):await agent.manage(agent.EmptyArguments(),replace(changed,original_message='另外买股票'))


@pytest.mark.asyncio
async def test_state_cas_owner_and_forbidden_proof(store):
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-card',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','closed':False,'command':{'kind':'planning'}}))
        db.add(WealthPlanORM(plan_id='foreign-plan',user_id='U2',version=1,status='AWAITING_CONFIRMATION',data={}))
        await db.commit()
        with pytest.raises(WealthPolicyError):
            await agent.save_state(db,'U1','NOVA-test-session','NW-card',1,WealthCardState(version=1,execution_plan_id='foreign-plan'))
        with pytest.raises(ValidationError):WealthCardState(version=1,proof='123456')
        with pytest.raises(ValidationError):agent.EmptyArguments(user_id='U2')
        with pytest.raises(ValidationError):agent.WealthIntent(kind='purchase',confirmed=True)
        rows=await agent.save_state(db,'U1','NOVA-test-session','NW-card',1,WealthCardState(version=1,closed=True))
        assert rows[0]['closed'] and rows[0]['version']==2
        with pytest.raises(WealthPolicyError):
            await agent.save_state(db,'U1','NOVA-test-session','NW-card',1,WealthCardState(version=1,closed=True))


@pytest.mark.asyncio
async def test_name_ambiguity_clarifies_but_recommend_opens_category_picker(store, monkeypatch):
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='purchase',product_queries=['同名']),agent.WealthIntent(kind='recommend')])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'}),
        call=AsyncMock(return_value=[{'product_id':'1','name':'同名','version':1},{'product_id':'2','name':'同名','version':1}])))
    async with store() as db:
        await conversation(db)
        result=await agent.manage(agent.EmptyArguments(),context(db))
        assert len(result['wealth_cards'])==2
        assert [c['command']['kind'] for c in result['wealth_cards']]==['clarify','recommend']
        assert result['wealth_cards'][1]['command']['category'] is None


@pytest.mark.asyncio
async def test_nova_prepare_saves_bank_reference_and_still_requires_mfa(store,http_store,monkeypatch):
    payload=await selected(store,monkeypatch)
    client,_,_=http_store
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-target',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':payload['context_id'],'closed':False,'command':{'kind':'purchase'}}))
        await db.commit()
    response=await client.post('/api/v1/wealth/target-purchases/prepare',json=payload,headers={'X-Nova-Wealth-Task':'NW-target'})
    assert response.status_code==200,response.text
    data=response.json()
    async with store() as db:
        row=await db.get(WealthConversationORM,'NW-target')
        assert row.data['execution_plan_id']==data['plan']['plan_id']
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
    base='/api/v1/wealth/plans/'+data['plan']['plan_id']
    assert (await client.post(base+'/execute',json={'version':1})).status_code==409
    await confirm_verify(client,data)
    result=await client.post(base+'/execute',json={'version':1})
    assert result.status_code==200 and result.json()['status']=='COMPLETED'


@pytest.mark.asyncio
async def test_nova_planning_continuation_persists_new_reference_and_new_mfa(store,http_store,monkeypatch):
    from tests.integration.test_wealth_incremental_flow import initialize,confirm_and_verify
    client,bank,source,plan=await initialize(store,http_store,monkeypatch)
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-planning',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':plan['context_id'],'closed':False,'command':{'kind':'planning'}}))
        await db.commit()
    response=await client.post(f"/api/v1/wealth/portfolio/plans/{plan['portfolio_id']}/prepare",
        json={'expected_version':1,'snapshot_id':plan['snapshot_id'],'payment_account_id':'A1'},headers={'X-Nova-Wealth-Task':'NW-planning'})
    assert response.status_code==200,response.text
    first=response.json()
    base,first_reference=await confirm_and_verify(client,first)
    assert (await client.post(base+'/execute',json={'version':1})).status_code==200
    async with store() as db:
        row=await db.get(WealthConversationORM,'NW-planning')
        assert row.data['execution_plan_id']==first['execution_plan_id']
        await agent.save_state(db,'U1','NOVA-test-session','NW-planning',row.version,WealthCardState(version=row.version,paused=True))
    blocked=await client.post(base+'/continue-purchase',json={'version':1})
    assert blocked.status_code==409,blocked.text
    async with store() as db:
        row=await db.get(WealthConversationORM,'NW-planning')
        await agent.save_state(db,'U1','NOVA-test-session','NW-planning',row.version,WealthCardState(version=row.version,paused=False))
    next_response=await client.post(base+'/continue-purchase',json={'version':1})
    assert next_response.status_code==200,next_response.text
    second=next_response.json()['prepared']
    async with store() as db:
        row=await db.get(WealthConversationORM,'NW-planning')
        assert row.data['execution_plan_id']==second['execution_plan_id']
    second_base='/api/v1/wealth/plans/'+second['execution_plan_id']
    assert (await client.post(second_base+'/execute',json={'version':1})).status_code==409
    _,second_reference=await confirm_and_verify(client,second)
    assert first_reference!=second_reference
    assert (await client.post(second_base+'/execute',json={'version':1})).status_code==200


def test_proof_redaction_preserves_product_code():
    from backend.app.services.bank.nova_conversations import redact_proof
    assert redact_proof('查看010969')=='查看010969'
    assert '123456' not in redact_proof('验证码：123456')


@pytest.mark.asyncio
async def test_concurrent_same_request_creates_one_card(store,monkeypatch):
    import asyncio
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='planning')])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'})))
    async with store() as db:await conversation(db)
    async def run():
        async with store() as db:return await agent.manage(agent.EmptyArguments(),context(db))
    left,right=await asyncio.gather(run(),run())
    assert left['wealth_cards'][0]['task_id']==right['wealth_cards'][0]['task_id']
    async with store() as db:assert len(await agent.cards(db,'U1','NOVA-test-session'))==1


@pytest.mark.asyncio
async def test_model_cannot_pick_unspecified_fund_but_explicit_ordinal_resolves(store,monkeypatch):
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='purchase',product_queries=['MF002'])])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'}),
        call=AsyncMock(return_value=[{'product_id':'MF002','name':'测试债券','version':1}])))
    async with store() as db:
        await conversation(db)
        first=await agent.manage(agent.EmptyArguments(),context(db))
        card=first['wealth_cards'][0]
        assert card['command']['kind']=='clarify' and not card['command']['products']
        row=await db.get(WealthConversationORM,card['task_id'])
        row.data={**row.data,'view_context':{'visible_products':[{'product_id':'MF002','product_version':1,'name':'测试债券'}]}}
        await db.commit()
        model.return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='purchase',task_id=card['task_id'],candidate_indices=[1])]))
        second=await agent.manage(agent.EmptyArguments(),replace(context(db,'request-second-wealth'),original_message='选择第一只产品'))
        assert second['wealth_cards'][0]['command']['products'][0]['product_id']=='MF002'


@pytest.mark.asyncio
async def test_edit_unsubmitted_plan_invalidates_old_authority_but_unknown_is_not_replaced(store,http_store,monkeypatch):
    payload=await selected(store,monkeypatch);client,_,_=http_store
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-edit',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':payload['context_id'],'closed':False,'command':{'kind':'purchase'}}))
        await db.commit()
    result=await client.post('/api/v1/wealth/target-purchases/prepare',json=payload,headers={'X-Nova-Wealth-Task':'NW-edit'})
    assert result.status_code==200,result.text
    plan_id=result.json()['plan']['plan_id']
    async with store() as db:
        row=await db.get(WealthConversationORM,'NW-edit')
        updated=await agent.save_state(db,'U1','NOVA-test-session','NW-edit',row.version,WealthCardState(version=row.version,reset_draft=True))
        assert updated[0].get('execution_plan_id')==plan_id
        assert (await db.get(WealthPlanORM,plan_id)).status=='AWAITING_CONFIRMATION'
        row=await db.get(WealthConversationORM,'NW-edit')
        updated=await agent.save_state(db,'U1','NOVA-test-session','NW-edit',row.version,
            WealthCardState(version=row.version,backtrack_token=updated[0]['pending_backtrack']['token'],accept_backtrack=True))
        assert not updated[0].get('execution_plan_id')
        plan=await db.get(WealthPlanORM,plan_id)
        assert plan.status=='CANCELLED'
        plan.status='EXECUTING';await db.commit()
        with pytest.raises(WealthPolicyError):await agent.discard_prepared(db,'U1',plan_id)
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
