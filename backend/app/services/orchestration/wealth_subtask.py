"""Member 2 adapter: same dialogue/graph service, no second orchestration engine."""
from sqlalchemy import select
from ...models.wealth import WealthConversationORM,WealthTaskControlORM
from ...domain.wealth_synthetic import fingerprint
from ...domain.time import utc_now
from ...integrations.wealth_authorization import WealthPolicyError,lock_profile
from ..ai.wealth_dialogue import message
from . import wealth_task
from .wealth_requirement_task import record


def receipt_key(user_id,request_id):
    return fingerprint(['wealth-subtask-create',user_id,request_id])


def envelope(task: dict, request_id: str | None=None, cards: dict | None=None) -> dict:
    return {'schema_version':'wealth-subtask-v1','child_task_id':task['task_id'],
        'session_id':task['session_id'],'parent_task_id':task.get('parent_task_id'),'request_id':request_id,
        'version':task['version'],'requirement_version':task.get('requirement_version'),
        'status':task['status'],'waiting_reason':task['waiting_reason'],
        'cards':cards or {},'result':task,'error':None,'code':None}


async def create(db,user_id,bank,request):
    key=receipt_key(user_id,request.request_id);digest=fingerprint(request.model_dump())
    previous=await db.get(WealthTaskControlORM,key)
    if previous:
        if previous.digest!=digest:raise WealthPolicyError('子任务请求编号冲突','IDEMPOTENCY_CONFLICT',409)
        return previous.result
    await db.rollback()
    scope=(await bank.call('POST','/contexts',{'subject_user_id':user_id}))['selected']
    if scope!=request.context_id:
        raise WealthPolicyError('子任务资金范围不是当前本人范围','STALE_CONTEXT',409)
    response=await message(db,user_id,bank,request.raw_text,request.session_id,request.version,
        request.request_id,parsed_intent=request.parsed_intent)
    task=response.get('task')
    if task is None:
        facts=await bank.assets(user_id)
        account=next((a for a in facts['cash'] if a['account_type']!='credit'),None)
        state=response.get('requirement_card') or {'schema_version':'wealth-requirements-v1','version':0,'fields':{}}
        conditional=response.get('conditional',False)
        task=await record(db,user_id,response['session_id'],scope,account['account_id'] if account else None,state,facts,
            waiting_reason='USER_INPUT' if response['kind'] in {'clarification','model_unavailable','assessment_required'} else None,
            result={'conditional':conditional,'response':response} if response['kind'] not in {'clarification','model_unavailable','assessment_required'} else None)
    row=await wealth_task.owned(db,user_id,task['task_id'])
    row.data={**row.data,'parent_task_id':request.parent_task_id,'create_request_id':request.request_id,
        'cards':response,'scope':{'user_id':user_id,'context_id':scope}}
    await db.commit()
    result=envelope(await wealth_task.view(db,row),request.request_id,response)
    db.add(WealthTaskControlORM(request_key=key,task_id=row.task_id,actor_id=user_id,action='subtask_create',
        digest=digest,result=result,occurred_at=utc_now()))
    await db.commit()
    return result


async def query(db,user_id,task_id):
    row=await wealth_task.owned(db,user_id,task_id)
    return envelope(await wealth_task.view(db,row),cards=row.data.get('cards',{}))


async def patch(db,user_id,bank,task_id,request):
    row=await wealth_task.owned(db,user_id,task_id)
    if row.version!=request.expected_version:
        raise WealthPolicyError('子任务版本已变化','STALE_TASK',409)
    session=await db.get(WealthConversationORM,row.session_id)
    if session is None or session.user_id!=user_id:
        raise WealthPolicyError('子任务会话不存在','NOT_FOUND',404)
    context_id=row.context_id;parent=row.data.get('parent_task_id')
    from ...schemas.wealth_subtask import SubtaskCreate
    return await create(db,user_id,bank,SubtaskCreate(raw_text=request.raw_text,parsed_intent=request.parsed_intent,
        request_id=request.request_id,session_id=session.session_id,version=session.version,
        context_id=context_id,parent_task_id=parent))


async def select_main(db,user_id,task_id,body):
    await lock_profile(db,user_id)
    task=await wealth_task.owned(db,user_id,task_id)
    key=user_id+':'+body['request_id'];digest=fingerprint(['select_main',task_id,body])
    old=await db.get(WealthTaskControlORM,key)
    if old:
        if old.digest!=digest:raise WealthPolicyError('请求编号冲突','IDEMPOTENCY_CONFLICT',409)
        return old.result
    if task.version!=body['expected_version']:raise WealthPolicyError('任务已变化','STALE_TASK',409)
    session=await db.get(WealthConversationORM,task.session_id)
    if session is None or session.user_id!=user_id:raise WealthPolicyError('会话不存在','NOT_FOUND',404)
    session.data={**session.data,'active_task':{'task_id':task_id,'version':task.version},
        'active_plan':{'plan_id':task.data['plan_id'],'version':task.data['plan_version']} if task.data.get('plan_id') else None,
        'requirements':task.data.get('requirements',session.data.get('requirements'))}
    session.version+=1
    result=envelope(await wealth_task.view(db,task),body['request_id'])
    result['session_version']=session.version
    db.add(WealthTaskControlORM(request_key=key,task_id=task_id,actor_id=user_id,action='select_main',digest=digest,result=result,occurred_at=utc_now()))
    await db.commit()
    return result
