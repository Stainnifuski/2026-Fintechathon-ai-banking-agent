"""Persist planning/clarification stages in the same graph store as bank operations."""
from uuid import uuid4
from sqlalchemy import select
from ...models.wealth import WealthTaskORM as Task, WealthTaskNodeORM as Node
from ...domain.wealth_task import validate_graph
from ...integrations.wealth_authorization import lock_profile, WealthPolicyError
from . import wealth_task


async def record(db,user_id,session_id,context_id,account_id,state,facts,prior_id=None,waiting_reason='USER_INPUT',result=None):
    await lock_profile(db,user_id)
    task=await wealth_task.owned(db,user_id,prior_id) if prior_id else None
    if task and (task.status in wealth_task.TERMINAL or task.data.get('plan_id')):
        task=None
    if task and (task.context_id!=context_id or task.control_mode!='AGENT' or task.status=='PAUSED'):
        raise WealthPolicyError('需求任务已暂停或资金范围变化','TASK_WRITE_BLOCKED',409)
    if task is None:
        task=Task(task_id='WT'+uuid4().hex,user_id=user_id,session_id=session_id,context_id=context_id,
            account_id=account_id or 'unselected',version=1,status='WAITING',control_mode='AGENT',data={})
        db.add(task);await db.flush()
    else:task.version+=1
    old=(await db.scalars(select(Node).where(Node.task_id==task.task_id))).all()
    for node in old:
        if node.status in {'READY','WAITING'} and node.kind in {'REQUIRE_INPUT','ASSESS','PLAN','RESEARCH'}:
            node.status='CANCELLED'
            node.data={**node.data,'superseded_by_requirement_version':state['version']}
    prefix=task.task_id+f':q{task.version:04}'
    graph=[{'node_id':prefix+':facts','kind':'READ_FACTS','depends_on':[],
        'completion_condition':'VERIFIED_BANK_FACTS','status':'COMPLETED','output':facts},
        {'node_id':prefix+':clarify','kind':'REQUIRE_INPUT','depends_on':[prefix+':facts'],
        'completion_condition':'NEXT_STAGE_REQUIREMENTS_RESOLVED','status':'WAITING' if waiting_reason else 'COMPLETED',
        'input':state,'waiting_reason':waiting_reason},
        {'node_id':prefix+':plan','kind':'RESEARCH' if result and result.get('conditional') else 'PLAN',
        'depends_on':[prefix+':clarify'],'completion_condition':'VALIDATED_RECOMMENDATION',
        'status':'COMPLETED' if result else 'WAITING','output':result}]
    if waiting_reason=='ASSESSMENT_REQUIRED':
        graph[1].update(kind='ASSESS',completion_condition='VALID_PERSONAL_RISK_ASSESSMENT')
    validate_graph([{'node_id':n.node_id,'kind':n.kind,'depends_on':n.depends_on,
        'completion_condition':n.data['completion_condition']} for n in old]+graph)
    for item in graph:
        db.add(Node(node_id=item['node_id'],task_id=task.task_id,kind=item['kind'],depends_on=item['depends_on'],
            status=item['status'],attempts=0,data={k:v for k,v in item.items() if k not in {'node_id','kind','depends_on','status'}}))
    task.account_id=account_id or task.account_id
    task.status='COMPLETED' if result else 'WAITING';task.waiting_reason=waiting_reason
    task.data={**task.data,'goal':'wealth_planning','requirements':state,'requirement_version':state['version'],
        'schema_version':'wealth-subtask-v1','result':result,'read_only':True}
    await db.commit()
    return await wealth_task.view(db,task)
