from dataclasses import replace
import time
import pytest
from sqlalchemy import select,func
from tests.integration.test_task_transfer_flow import db
from backend.app.services.ai import task_presentation as presentation,task_flow,execution_queue as queue
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from backend.app.services.bank import nova_conversations as chats
from backend.app.models.nova_task import NovaSessionORM
from backend.app.models.nova_execution import NovaExecutionORM
from backend.app.models.nova_conversation import NovaConversationMessageORM
from backend.app.models.account import AccountORM


async def seed(db,lease=0):
    conversation=await chats.create(db,'U1')
    session=conversation['session_id']
    db.add(NovaSessionORM(session_id=session,user_id='U1',state={'tasks':[], 'calendar_tasks':[
        {'entry_id':'calendar-draft','origin_request_id':'initial-request','closed':False,
         'prefill':{'title':'测试日程'},'timezone':'Asia/Shanghai','event_id':None}]}))
    db.add(NovaExecutionORM(conversation_id=session,user_id='U1',version=1,payload_ciphertext=queue.encode({
        'queue_id':'q-test','request_id':'initial-request','status':'waiting','lease_until':lease,
        'steps':[{'goal_id':'done','status':'completed'}, {'goal_id':'current','status':'waiting'},
                 {'goal_id':'later-a','status':'pending'},{'goal_id':'later-b','status':'pending'}]})))
    await db.commit()
    return ToolContext('U1',db,PageContext(),session_id=session)


@pytest.mark.asyncio
async def test_handoff_preserves_form_bank_and_stops_every_future_goal(db):
    ctx=await seed(db)
    before=[(row.account_id,row.balance) for row in (await db.scalars(select(AccountORM))).all()]
    result=await presentation.release(ctx,'calendar:calendar-draft','manual','handoff-request',{'title':'新标题','day':'2026-10-25'})
    assert result['cancelled_count']==2 and result['draft']['title']=='新标题'
    assert not await task_flow.active(ctx)
    state=queue.unpack(await queue.load(ctx))
    assert state['status']=='stopped'
    assert [s['status'] for s in state['steps']]==['completed','stopped','stopped','stopped']
    row=await db.get(NovaSessionORM,ctx.session_id)
    assert not row.state['calendar_tasks'][0]['closed']
    assert row.state['calendar_tasks'][0]['agent_released']
    assert before==[(row.account_id,row.balance) for row in (await db.scalars(select(AccountORM))).all()]
    again=await presentation.release(ctx,'calendar:calendar-draft','manual','handoff-request')
    assert again==result
    with pytest.raises(chats.ConversationError):
        await presentation.release(ctx,'calendar:calendar-draft','manual','different-request')


@pytest.mark.asyncio
async def test_foreign_owner_and_busy_queue_do_not_release_anything(db):
    ctx=await seed(db,lease=time.time()+120)
    with pytest.raises(chats.ConversationError):
        await presentation.release(replace(ctx,user_id='U2'),'calendar:calendar-draft','manual','foreign-request')
    await db.rollback()
    with pytest.raises(chats.ConversationError):
        await presentation.release(ctx,'calendar:calendar-draft','manual','busy-request')
    await db.rollback()
    assert not await presentation.released(ctx)
    assert queue.unpack(await queue.load(ctx))['status']=='waiting'


@pytest.mark.asyncio
async def test_unknown_or_secret_draft_never_changes_queue(db):
    ctx=await seed(db)
    for key,draft in [('calendar:missing',{}),('calendar:calendar-draft',{'code':'123456'}),('calendar:calendar-draft',{'note':'x'*13000})]:
        with pytest.raises(chats.ConversationError):
            await presentation.release(ctx,key,'manual','invalid-request',draft)
        await db.rollback()
    assert not await presentation.released(ctx)
    assert queue.unpack(await queue.load(ctx))['status']=='waiting'


@pytest.mark.asyncio
async def test_position_is_owner_scoped_and_never_adds_fake_user_message(db):
    ctx=await seed(db)
    await presentation.position(ctx,'calendar:calendar-draft','action-test-123')
    await presentation.position(ctx,'calendar:calendar-draft','action-test-123')
    rows=(await db.scalars(select(NovaConversationMessageORM))).all()
    assert len(rows)==1 and rows[0].role=='assistant'
    items=await presentation.inventory(ctx)
    assert items[0]['position']=='action-test-123'
    await presentation.release(ctx,'calendar:calendar-draft','manual','release-test')
    with pytest.raises(chats.ConversationError):
        await presentation.position(ctx,'calendar:calendar-draft','action-after-release')


@pytest.mark.asyncio
async def test_other_domain_needs_switch_but_auxiliary_read_does_not(db):
    ctx=await seed(db)
    assert (await task_flow.active(ctx))['type']=='calendar'
    assert not await task_flow.guard_tool(ctx,'get_current_time')
    proposal=await task_flow.guard_tool(replace(ctx,original_message='办理转账'),'manage_transfer_task')
    assert proposal['task_switch']['current_label']=='日程'


@pytest.mark.asyncio
async def test_partial_calendar_continuation_keeps_the_original_task_id(db):
    from backend.app.services.ai import nova
    import json
    ctx=await seed(db)
    session=await db.get(NovaSessionORM,ctx.session_id,populate_existing=True)
    session.state={**session.state,'calendar_tasks':[{'kind':'mutation','entry_id':'calendar-draft',
        'origin_request_id':'initial-request','action':'add','timezone':'Asia/Shanghai',
        'filters':{},'changes':{'title':'测试日程'},'targets':[],'snapshots':{},
        'status':'PENDING','closed':False}]}
    await db.commit()
    ctx=await task_flow.focus(ctx)
    ctx=replace(ctx,request_id='continue-calendar',original_message='测试日程的地点在演示楼')
    result=await nova.execute_tool({'function':{'name':'manipulate_calender_event','arguments':json.dumps({
        'action':'add','task_entry_id':'calendar-draft','location':'演示楼'},ensure_ascii=False)}},ctx)
    assert not result.get('error'),result
    session=await db.get(NovaSessionORM,ctx.session_id,populate_existing=True)
    assert len(session.state['calendar_tasks'])==1
    assert session.state['calendar_tasks'][0]['entry_id']=='calendar-draft'
    assert session.state['calendar_tasks'][0]['changes']['location']=='演示楼'


@pytest.mark.asyncio
async def test_another_same_domain_operation_cannot_create_parallel_card(db):
    from backend.app.services.ai import nova
    import json
    ctx=await seed(db)
    proposal=await nova.execute_tool({'function':{'name':'manipulate_calender_event','arguments':json.dumps({
        'action':'add','title':'另一条日程','start_date':'2026-10-26','start_time':'12:00'})}},
        replace(ctx,request_id='new-calendar',original_message='新建另一条日程'))
    assert proposal['task_switch']
    session=await db.get(NovaSessionORM,ctx.session_id,populate_existing=True)
    assert len(session.state['calendar_tasks'])==1


@pytest.mark.asyncio
async def test_form_snapshot_is_not_authorization_and_does_not_expose_phone_to_model(db):
    ctx=await seed(db)
    await presentation.save_draft(ctx,'calendar:calendar-draft',{'title':'本人表单填写','phone':'13800000000'})
    current=await task_flow.active(ctx)
    assert current['unsubmitted_form']=={'title':'本人表单填写'}
    assert queue.unpack(await queue.load(ctx))['status']=='waiting'
    with pytest.raises(chats.ConversationError):
        await presentation.save_draft(ctx,'calendar:calendar-draft',{'verification_code':'123456'})
    await db.rollback()
    entry=await presentation.continuation_entry(replace(ctx,active_entry_type='calendar',active_entry_id='calendar-draft'),'calendar')
    assert entry['prefill']['title']=='本人表单填写'
    await presentation.release(ctx,'calendar:calendar-draft','manual','release-snapshot')
    with pytest.raises(chats.ConversationError):
        await presentation.save_draft(ctx,'calendar:calendar-draft',{'title':'不应再写入'})
