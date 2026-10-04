"""Member 2 adapter: same dialogue/graph service, no second orchestration engine."""
from sqlalchemy import select
from uuid import uuid4
from ...models.wealth import WealthConversationORM,WealthTaskControlORM,WealthTaskORM,WealthTaskNodeORM
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
    if request.external_dependencies:
        facts=await bank.assets(user_id)
        accounts=[a for a in facts['cash'] if a['account_type']!='credit']
        if len(accounts)!=1:raise WealthPolicyError('外部依赖需唯一明确账户','ACCOUNT_SELECTION_REQUIRED',409)
        sid='WC'+uuid4().hex;tid='WT'+uuid4().hex
        task=WealthTaskORM(task_id=tid,user_id=user_id,session_id=sid,context_id=scope,
            account_id=accounts[0]['account_id'],version=1,status='WAITING',control_mode='AGENT',waiting_reason='EXTERNAL_EVENT',
            data={'goal':'wealth_planning','read_only':True,'parent_task_id':request.parent_task_id,
                'deferred_request':request.model_dump(),'schema_version':'wealth-subtask-v1'})
        db.add(WealthConversationORM(session_id=sid,user_id=user_id,version=1,data={
            'context_id':scope,'history':[],'active_task':{'task_id':tid,'version':1}}))
        db.add(task);await db.flush()
        for ordinal,name in enumerate(request.external_dependencies):
            db.add(WealthTaskNodeORM(node_id=tid+f':external:{ordinal}',task_id=tid,kind='REQUIRE_INPUT',
                depends_on=[],status='WAITING',attempts=0,data={'completion_condition':'VERIFIED_PARENT_BANK_EVENT',
                    'dependency_name':name,'waiting_reason':'EXTERNAL_EVENT','parent_task_id':request.parent_task_id}))
        await db.commit()
        result=envelope(await wealth_task.view(db,task),request.request_id,
            {'message':'等待已登记的上层银行事件；完成后重读实际现金，再解释原理财需求。未授予交易权限。'})
        db.add(WealthTaskControlORM(request_key=key,task_id=tid,actor_id=user_id,action='subtask_create',
            digest=digest,result=result,occurred_at=utc_now()))
        await db.commit();return result
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


async def complete_external(db,user_id,bank,task_id,request):
    task=await wealth_task.owned(db,user_id,task_id)
    key=fingerprint(['wealth-external-completion',user_id,task_id,request.request_id]);digest=fingerprint(request.model_dump())
    old=await db.get(WealthTaskControlORM,key)
    if old:
        if old.digest!=digest:raise WealthPolicyError('事件请求编号冲突','IDEMPOTENCY_CONFLICT',409)
        return old.result
    node=await db.get(WealthTaskNodeORM,request.node_id)
    resuming=task.data.get('external_processing')=={'key':key,'digest':digest}
    if (task.version!=request.expected_version+(1 if resuming else 0) or task.status!='WAITING' or not task.data.get('deferred_request')
        or task.data.get('parent_task_id')!=request.parent_task_id or node is None or node.task_id!=task_id
        or node.data.get('completion_condition')!='VERIFIED_PARENT_BANK_EVENT' or node.status!=('COMPLETED' if resuming else 'WAITING')):
        raise WealthPolicyError('不存在可完成的当前外部依赖','EXTERNAL_DEPENDENCY_INVALID',409)
    scope=(await bank.call('POST','/contexts',{'subject_user_id':user_id}))['selected']
    if scope!=task.context_id:raise WealthPolicyError('外部依赖范围已经变化','STALE_CONTEXT',409)
    evidence=await bank.call('POST','/external-evidence',{'subject_user_id':user_id,
        'account_id':task.account_id,'event_reference':request.event_reference})
    await lock_profile(db,user_id)
    task=await wealth_task.owned(db,user_id,task_id);node=await db.get(WealthTaskNodeORM,request.node_id,populate_existing=True)
    if task.version!=request.expected_version+(1 if resuming else 0) or node.status!=('COMPLETED' if resuming else 'WAITING'):
        raise WealthPolicyError('事件版本已改变','STALE_TASK',409)
    if not resuming and any((n.data.get('output') or {}).get('event_reference')==request.event_reference for n in
        (await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task_id))).all()):
        raise WealthPolicyError('同一银行事件不能满足两个不同依赖','EXTERNAL_EVIDENCE_REUSED',409)
    if not resuming:
        node.status='COMPLETED';node.data={**node.data,'output':evidence,'waiting_reason':None};task.version+=1
        task.data={**task.data,'external_processing':{'key':key,'digest':digest}}
    await db.commit()
    nodes=(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task_id))).all()
    if all(n.status=='COMPLETED' for n in nodes):
        from ...schemas.wealth_subtask import SubtaskCreate
        deferred=SubtaskCreate.model_validate(task.data['deferred_request'])
        child=await create(db,user_id,bank,deferred.model_copy(update={'external_dependencies':[],
            'request_id':fingerprint(['resume-external',task_id]),'parent_task_id':task_id}))
        task=await wealth_task.owned(db,user_id,task_id)
        task.status='COMPLETED';task.waiting_reason=None;task.data={**task.data,'child_task_id':child['child_task_id']}
        result={**envelope(await wealth_task.view(db,task),request.request_id),'continuation':child}
    else:result=envelope(await wealth_task.view(db,task),request.request_id)
    task.data={k:v for k,v in task.data.items() if k!='external_processing'}
    db.add(WealthTaskControlORM(request_key=key,task_id=task_id,actor_id=user_id,action='external_complete',
        digest=digest,result=result,occurred_at=utc_now()))
    await db.commit();return result


async def patch(db,user_id,bank,task_id,request):
    row=await wealth_task.owned(db,user_id,task_id)
    key=fingerprint(['wealth-subtask-patch',user_id,task_id,request.request_id])
    digest=fingerprint(request.model_dump())
    previous=await db.get(WealthTaskControlORM,key)
    if previous:
        if previous.digest!=digest:raise WealthPolicyError('修订请求编号冲突','IDEMPOTENCY_CONFLICT',409)
        return previous.result
    if row.version!=request.expected_version:
        raise WealthPolicyError('子任务版本已变化','STALE_TASK',409)
    session=await db.get(WealthConversationORM,row.session_id)
    if session is None or session.user_id!=user_id:
        raise WealthPolicyError('子任务会话不存在','NOT_FOUND',404)
    context_id=row.context_id;parent=row.data.get('parent_task_id')
    from ...schemas.wealth_subtask import SubtaskCreate
    result=await create(db,user_id,bank,SubtaskCreate(raw_text=request.raw_text,parsed_intent=request.parsed_intent,
        request_id=key,session_id=session.session_id,version=session.version,
        context_id=context_id,parent_task_id=parent))
    result={**result,'request_id':request.request_id}
    db.add(WealthTaskControlORM(request_key=key,task_id=task_id,actor_id=user_id,action='subtask_patch',
        digest=digest,result=result,occurred_at=utc_now()))
    await db.commit()
    return result


async def select_main(db,user_id,task_id,body,bank):
    scope=(await bank.call('POST','/contexts',{'subject_user_id':user_id}))['selected']
    await lock_profile(db,user_id)
    task=await wealth_task.owned(db,user_id,task_id)
    if task.context_id!=scope:raise WealthPolicyError('主任务不在当前资金范围','STALE_CONTEXT',409)
    key=user_id+':'+body['request_id'];digest=fingerprint(['select_main',task_id,body])
    old=await db.get(WealthTaskControlORM,key)
    if old:
        if old.digest!=digest:raise WealthPolicyError('请求编号冲突','IDEMPOTENCY_CONFLICT',409)
        return old.result
    if task.version!=body['expected_version']:raise WealthPolicyError('任务已变化','STALE_TASK',409)
    session=await db.get(WealthConversationORM,task.session_id)
    if session is None or session.user_id!=user_id:raise WealthPolicyError('会话不存在','NOT_FOUND',404)
    view=await wealth_task.view(db,task)
    command=next((n.get('command') for n in reversed(view['nodes'])
        if n['kind']=='SUBMIT' and n.get('command') and n['status']!='CANCELLED'),None)
    session.data={**session.data,'active_task':{'task_id':task_id,'version':task.version},
        'active_command':command,
        'active_plan':{'plan_id':task.data['plan_id'],'version':task.data['plan_version']} if task.data.get('plan_id') else None,
        'requirements':task.data.get('requirements',session.data.get('requirements'))}
    session.version+=1
    result=envelope(await wealth_task.view(db,task),body['request_id'])
    result['session_version']=session.version
    db.add(WealthTaskControlORM(request_key=key,task_id=task_id,actor_id=user_id,action='select_main',digest=digest,result=result,occurred_at=utc_now()))
    await db.commit()
    return result
