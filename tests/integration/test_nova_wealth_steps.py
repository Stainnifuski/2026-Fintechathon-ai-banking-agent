from dataclasses import replace
import pytest
from backend.app.models.wealth import WealthConversationORM,WealthPlanORM
from backend.app.routers.nova import WealthCardState
from backend.app.services.ai import wealth_agent as agent,wealth_steps
from backend.app.integrations.wealth_authorization import WealthPolicyError
from backend.app.services.orchestration.wealth_nova_guard import require_no_pending_backtrack
from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import conversation,context
from tests.integration.test_nova_wealth_cards import fake_model


def data():
    return {'nova_session_id':'NOVA-test-session','context_id':'test','closed':False,
        'command':{'kind':'recommend','category':'bond_fund','products':[{'product_id':'B017','product_version':1,'name':'测试'}],
            'amounts_yuan':['100'],'cash_field':None,'cash_yuan':None},
        'view_context':{'completed_stages':['match','candidates','selected','amounts'],
            'selected_products':[{'product_id':'B017','product_version':1,'name':'测试'}],
            'amounts_yuan':{'B017':'100'}}}


@pytest.mark.asyncio
async def test_natural_backtrack_proposes_then_cancel_or_manual_accept(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='recommend',task_id='NW-step',edit_kind='backtrack',target_stage='candidates'))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-step',user_id='U1',version=1,data=data()));await db.commit()
        ctx=replace(context(db),wealth_task_id='NW-step',wealth_stage='amounts',original_message='回到产品选择')
        card=(await agent.manage(agent.EmptyArguments(),ctx))['wealth_cards'][0]
        assert card['command']['amounts_yuan']==['100']
        cancelled=(await agent.save_state(db,'U1',ctx.session_id,'NW-step',card['version'],
            WealthCardState(version=card['version'],backtrack_token=card['pending_backtrack']['token'])))[0]
        assert cancelled['command']['amounts_yuan']==['100'] and not cancelled.get('pending_backtrack')
        row=await agent.require_card(db,'U1','NW-step');await agent.propose_backtrack(db,'U1',row,'candidates');await db.commit()
        version=row.version;token=row.data['pending_backtrack']['token']
        accepted=(await agent.save_state(db,'U1',ctx.session_id,'NW-step',version,
            WealthCardState(version=version,backtrack_token=token,accept_backtrack=True)))[0]
        assert accepted['view_context']['completed_stages']==['match']
        assert accepted['view_context']['amounts_yuan']=={}
        assert accepted['command']['products']==[]
        with pytest.raises(WealthPolicyError):
            await agent.save_state(db,'U1',ctx.session_id,'NW-step',accepted['version'],
                WealthCardState(version=accepted['version'],backtrack_token=token,accept_backtrack=True))


@pytest.mark.asyncio
async def test_finished_amount_edit_needs_confirmation_but_current_correction_does_not(store,monkeypatch):
    intent=agent.WealthIntent(kind='recommend',task_id='NW-step',amounts_yuan=['200'])
    fake_model(monkeypatch,intent)
    async with store() as db:
        await conversation(db)
        row=WealthConversationORM(session_id='NW-step',user_id='U1',version=1,data=data());db.add(row);await db.commit()
        ctx=replace(context(db),wealth_task_id='NW-step',wealth_stage='amounts',original_message='改成200元')
        card=(await agent.manage(agent.EmptyArguments(),ctx))['wealth_cards'][0]
        assert card['pending_backtrack']['stage']=='amounts'
        assert card['command']['amounts_yuan']==['100']
        row=await db.get(WealthConversationORM,'NW-step');reset=data();reset['view_context']['completed_stages']=['match','candidates','selected'];row.data=reset;await db.commit()
        card=(await agent.manage(agent.EmptyArguments(),replace(ctx,request_id='current-edit')))['wealth_cards'][0]
        assert not card.get('pending_backtrack') and card['command']['amounts_yuan']==['200']
        assert card['command']['auto_prepare']


@pytest.mark.asyncio
@pytest.mark.parametrize('tracked',[True,False])
async def test_settled_redemption_barrier_survives_new_buy_and_pending_blocks_authority(store,tracked):
    async with store() as db:
        await conversation(db)
        payload=data();payload.update(execution_plan_id='BUY-new',committed_stage_ids=['SELL-paid'] if tracked else [])
        row=WealthConversationORM(session_id='NW-step',user_id='U1',version=1,data=payload);db.add(row)
        db.add_all([WealthPlanORM(plan_id='SELL-paid',user_id='U1',version=1,status='COMPLETED',data={'items':[{'status':'SETTLED'}],'incremental_continuation':{'prepared':{'execution_plan_id':'BUY-new'}}}),
                    WealthPlanORM(plan_id='BUY-new',user_id='U1',version=1,status='AWAITING_CONFIRMATION',data={'items':[]})]);await db.commit()
        with pytest.raises(WealthPolicyError,match='已有成交'):
            await agent.propose_backtrack(db,'U1',row,'match')
        row.data={**row.data,'pending_backtrack':{'token':'pending'}};await db.commit()
        with pytest.raises(WealthPolicyError) as error:await require_no_pending_backtrack(db,'U1','BUY-new')
        assert error.value.code=='BACKTRACK_PENDING'
        await require_no_pending_backtrack(db,'U2','BUY-new')


def test_dependency_reset_keeps_independent_pool_and_clears_budget():
    value=data();value['command']['kind']='planning';value['command']['cash_yuan']='1000'
    value['view_context'].update(completed_stages=['match','cash_management','stable','growth','selected','budget'],
        cash_yuan='1000',selected_products=[{'product_id':'B017','bucket':'cash_management'},{'product_id':'B018','bucket':'stable'}])
    result=wealth_steps.reset_data(value,'stable')
    assert result['view_context']['completed_stages']==['match','cash_management','growth']
    assert result['view_context']['selected_products']==[{'product_id':'B017','bucket':'cash_management'}]
    assert result['command']['cash_yuan'] is None
    assert value['command']['cash_yuan']=='1000'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,target,expected',[('planning','trade','allocation'),('recommend','trade','purchase_review'),('redeem','trade','redemption_input'),('purchase','candidates','match')])
async def test_generated_confirmation_backtrack_reopens_preparation_instead_of_empty_step(store,kind,target,expected):
    async with store() as db:
        await conversation(db)
        payload=data();payload['command']['kind']=kind
        if kind=='purchase':payload['command']['category']=None
        row=WealthConversationORM(session_id='NW-step',user_id='U1',version=1,data=payload);db.add(row);await db.commit()
        await agent.propose_backtrack(db,'U1',row,target)
        assert row.data['pending_backtrack']['stage']==expected
        reset=wealth_steps.reset_data(row.data,expected)
        assert reset['reopen_stage']==expected
        assert expected not in reset['view_context']['completed_stages']
