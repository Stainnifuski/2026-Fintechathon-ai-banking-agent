"""本人任务控制和操作员接管；不接受客户端指定主体或角色。"""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..schemas.wealth import StrictRequest
from ..models.wealth import WealthTaskORM, WealthConversationORM, WealthPlanORM
from ..services.orchestration import wealth_task as tasks
from ..integrations.wealth_authorization import WealthPolicyError
from ..integrations.wealth_bank_api import HttpWealthBankApi
from .wealth import get_wealth_bank
from ..schemas.wealth_subtask import SubtaskCreate,SubtaskPatch,ExternalCompletion
from ..services.orchestration import wealth_subtask

router = APIRouter(prefix='/api/v1/wealth', tags=['wealth-tasks'])


@router.post('/subtasks')
async def create_subtask(request:SubtaskCreate,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    try:return await wealth_subtask.create(db,user['user_id'],bank,request)
    except WealthPolicyError as error:await db.rollback();fail(error)


@router.get('/subtasks/{task_id}')
async def get_subtask(task_id:str,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    try:return await wealth_subtask.query(db,user['user_id'],task_id)
    except WealthPolicyError as error:fail(error)


@router.post('/subtasks/{task_id}/external-completed')
async def external_completed(task_id:str,request:ExternalCompletion,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..integrations.bank_api import BankApiError
    try:return await wealth_subtask.complete_external(db,user['user_id'],bank,task_id,request)
    except (WealthPolicyError,BankApiError) as error:await db.rollback();fail(error)


@router.patch('/subtasks/{task_id}')
async def patch_subtask(task_id:str,request:SubtaskPatch,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    try:return await wealth_subtask.patch(db,user['user_id'],bank,task_id,request)
    except WealthPolicyError as error:await db.rollback();fail(error)


class TaskControl(StrictRequest):
    request_id: str = Field(min_length=8, max_length=64)
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(default='', max_length=1000)


@router.post('/tasks/{task_id}/select-main')
async def select_main(task_id:str,request:TaskControl,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    try:return await wealth_subtask.select_main(db,user['user_id'],task_id,request.model_dump(),bank)
    except WealthPolicyError as error:await db.rollback();fail(error)


def fail(error):
    raise HTTPException(error.status_code, detail={'code':error.code, 'message':error.message}) from error


@router.get('/tasks')
async def list_tasks(user:dict=Depends(get_current_user), db:AsyncSession=Depends(get_db),
    bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..integrations.bank_api import BankApiError
    try: context=(await bank.call('POST','/contexts',{'subject_user_id':user['user_id']}))['selected']
    except BankApiError as error: fail(error)
    rows = (await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id==user['user_id'])
        .order_by(WealthTaskORM.updated_at.desc()))).all()
    return {'items':[await tasks.view(db, row) for row in rows if row.context_id==context]}


@router.get('/tasks/{task_id}')
async def get_task(task_id:str, user:dict=Depends(get_current_user), db:AsyncSession=Depends(get_db)):
    try: return await tasks.view(db, await tasks.owned(db, user['user_id'], task_id))
    except WealthPolicyError as error: fail(error)


@router.post('/tasks/{task_id}/repreview')
async def repreview_task(task_id:str,request:TaskControl,user:dict=Depends(get_current_user),
    db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    """Renew only an unsubmitted single action; never apply operator notes as commands."""
    from uuid import uuid4
    from ..models.wealth import WealthTaskNodeORM
    from ..schemas.wealth import InvestmentCommand
    from ..integrations import wealth_authorization as policy
    from ..integrations.bank_api import BankApiError,BankResponseUnknown
    try:
        row=await tasks.owned(db,user['user_id'],task_id)
        if row.version!=request.expected_version or row.control_mode!='AGENT' or row.status!='WAITING':
            raise WealthPolicyError('请先交还并继续任务，再重新核对当前版本','STALE_TASK',409)
        nodes=list(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task_id,
            WealthTaskNodeORM.kind=='SUBMIT',WealthTaskNodeORM.status!='CANCELLED')))
        if row.data.get('plan_id') or len(nodes)!=1:
            raise WealthPolicyError('整份方案须回到配置核对，修订后重新准备差额','PLAN_REVIEW_REQUIRED',409)
        if nodes[0].status in {'RUNNING','UNKNOWN','COMPLETED'}:
            raise WealthPolicyError('已提交或结果未知，请按原号查询，不能创建替代预览','RECONCILIATION_REQUIRED',409)
        original=InvestmentCommand.model_validate(nodes[0].data['command'])
        if original.plan_id:
            raise WealthPolicyError('整份方案须重新核对全部动作','PLAN_REVIEW_REQUIRED',409)
        context_id=row.context_id
        await db.rollback()
        try:
            await bank.query(user['user_id'],original.operation_id)
        except BankApiError as error:
            if isinstance(error,BankResponseUnknown) or error.status_code!=404 or error.code!='NOT_FOUND':raise
        else:
            raise WealthPolicyError('银行已有原号记录，须核对原订单','RECONCILIATION_REQUIRED',409)
        facts=await bank.assets(user['user_id'])
        if facts.get('context_id','legacy-v1')!=context_id:
            raise WealthPolicyError('原任务资金范围已变化，请重新选择当前范围','CONTEXT_MISMATCH',409)
        command=original.model_copy(update={'operation_id':'WTmanual'+uuid4().hex})
        redemption=None
        if command.direction=='redeem':
            redemption=await bank.call('POST','/redemptions/preview',{
                'subject_user_id':user['user_id'],'operation_id':command.operation_id,
                'context_id':context_id,
                'position_key':f'{command.account_id}:{command.product_id}:{command.product_version}',
                'input_mode':'units','units':command.units,
                'redemption_mode':command.redemption_mode or 'standard'})
            view=redemption['preview']
            resolved=InvestmentCommand.model_validate(view['command'])
            if any(getattr(resolved,key)!=getattr(command,key) for key in
                   ('operation_id','direction','account_id','product_id','product_version','disclosure_version','units')):
                raise WealthPolicyError('持仓或披露版本已变化，请重新选择当前持仓','DATA_CHANGED',409)
            command=resolved
        else:
            view=await bank.precheck(user['user_id'],command)
        # Recheck under the same user lock before revising the persisted task.
        await policy.lock_profile(db,user['user_id'])
        current=await tasks.owned(db,user['user_id'],task_id)
        if current.version!=request.expected_version or current.control_mode!='AGENT' or current.status!='WAITING':
            raise WealthPolicyError('任务已变化，请刷新后重新核对','STALE_TASK',409)
        task=await tasks.revise_command(db,user['user_id'],task_id,request.expected_version,command)
        auth=await policy.new_preview_authorization(db,user['user_id'],command)
        current=await tasks.owned(db,user['user_id'],task_id)
        if current.version!=task['version'] or current.control_mode!='AGENT' or current.status!='WAITING':
            raise WealthPolicyError('任务控制已变化，请重新核对','STALE_TASK',409)
        preview={**view,'command':command.model_dump(),'authorization_id':auth.authorization_id,
            'authorization_status':auth.status,'digest':auth.param_digest,'expires_at':policy.aware(auth.expires_at).isoformat()}
        await db.commit()
        return {'task':task,'preview':preview,'redemption':redemption}
    except (WealthPolicyError,BankApiError) as error:
        await db.rollback();fail(error)


@router.post('/tasks/{task_id}/{action}')
async def control_task(task_id:str, action:Literal['pause','resume','rollback','cancel','handoff'], request:TaskControl,
    user:dict=Depends(get_current_user), db:AsyncSession=Depends(get_db)):
    try: return await tasks.control(db, user['user_id'], task_id, action, request.model_dump())
    except WealthPolicyError as error:
        await db.rollback(); fail(error)


async def operator(user:dict=Depends(get_current_user)):
    if not tasks.operator_allowed(user['user_id']):
        raise HTTPException(403, detail={'code':'OPERATOR_REQUIRED','message':'需要服务端操作员角色'})
    return user


@router.get('/operator/tasks')
async def operator_tasks(user:dict=Depends(operator), db:AsyncSession=Depends(get_db)):
    rows = (await db.scalars(select(WealthTaskORM).where((WealthTaskORM.control_mode=='HANDOFF_PENDING') | ((WealthTaskORM.control_mode=='HUMAN') & (WealthTaskORM.owner_id==user['user_id']))))).all()
    return {'items':[await tasks.view(db, row) for row in rows]}


@router.get('/operator/tasks/{task_id}/facts')
async def operator_facts(task_id:str, user:dict=Depends(operator), db:AsyncSession=Depends(get_db),
    bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..integrations.bank_api import BankApiError
    row=await db.get(WealthTaskORM,task_id)
    if not row or row.control_mode!='HUMAN' or row.owner_id!=user['user_id']:
        raise HTTPException(403,detail={'code':'HANDOFF_CONFLICT','message':'只有领取操作员可查看处理事实'})
    subject, account_id, context_id, version = row.user_id, row.account_id, row.context_id, row.version
    await db.rollback()
    try: facts = await bank.call('POST','/assets',{'subject_user_id':subject,'account_id':account_id})
    except BankApiError as error: fail(error)
    current = await db.get(WealthTaskORM, task_id, populate_existing=True)
    if (not current or current.version != version or current.control_mode != 'HUMAN'
            or current.owner_id != user['user_id']):
        raise HTTPException(409, detail={'code':'HANDOFF_CONFLICT','message':'任务已变化，请重新领取或核对'})
    if (facts.get('context_id','legacy-v1') != context_id
            or account_id not in {a['account_id'] for a in facts.get('cash',[])}):
        raise HTTPException(409, detail={'code':'CONTEXT_MISMATCH','message':'原任务资金范围已变化，不能读取其他范围资料'})
    return facts


@router.get('/operator/tasks/{task_id}/context')
async def operator_context(task_id:str, user:dict=Depends(operator), db:AsyncSession=Depends(get_db),
        bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    row = await db.get(WealthTaskORM, task_id)
    if not row or row.control_mode != 'HUMAN' or row.owner_id != user['user_id']:
        raise HTTPException(403, detail={'code':'HANDOFF_CONFLICT','message':'只有领取操作员可查看处理上下文'})
    conversation = await db.get(WealthConversationORM, row.session_id)
    if conversation and (conversation.user_id != row.user_id or conversation.data.get('context_id','legacy-v1') != row.context_id):
        raise HTTPException(409, detail={'code':'CONTEXT_MISMATCH','message':'原会话范围需要核对'})
    plan = await db.get(WealthPlanORM, row.data['plan_id']) if row.data.get('plan_id') else None
    if plan and (plan.user_id != row.user_id or plan.data.get('request',{}).get('account_id') != row.account_id
            or plan.data.get('context_id','legacy-v1') != row.context_id
            or plan.version != row.data.get('plan_version',plan.version)):
        raise HTTPException(409, detail={'code':'CONTEXT_MISMATCH','message':'原方案范围需要核对'})
    conversation_view={'session_id':conversation.session_id,'version':conversation.version,
        'history':[{'user':turn.get('user',''),'reply':{'message':turn.get('reply',{}).get('message','')}}
            for turn in conversation.data.get('history',[])]} if conversation else None
    plan_view={'plan_id':plan.plan_id,'version':plan.version,'status':plan.status,
        'data':{'items':[{'command':item['command'],'status':item.get('status','UNKNOWN')}
            for item in plan.data.get('items',[])]}} if plan else None
    portfolio=None
    if plan and plan.data.get('portfolio_plan') and plan.data.get('portfolio_snapshot'):
        from copy import deepcopy
        saved=deepcopy(plan.data['portfolio_plan']);snapshot=deepcopy(plan.data['portfolio_snapshot'])
        if (saved.get('portfolio_id')!=plan.data.get('portfolio_id')
                or saved.get('version')!=plan.data.get('portfolio_version')
                or saved.get('snapshot_id')!=snapshot.get('snapshot_id')
                or snapshot.get('snapshot_id')!=plan.data.get('portfolio_snapshot_id')
                or saved.get('context_id')!=row.context_id or snapshot.get('context_id')!=row.context_id
                or saved.get('asset_scope_id')!=snapshot.get('asset_scope_id')
                or saved.get('basis_fen')!=snapshot.get('basis_fen')
                or row.account_id not in snapshot.get('account_ids',[])):
            raise HTTPException(409,detail={'code':'CONTEXT_MISMATCH','message':'整体方案和原快照引用不一致，请重新核对'})
        # Historical allocation is explanatory, but every account must still be authorized.
        facts=await operator_facts(task_id,user,db,bank)
        if not set(snapshot.get('account_ids',[]))<=set(a['account_id'] for a in facts.get('cash',[])):
            raise HTTPException(409,detail={'code':'CONTEXT_MISMATCH','message':'整体快照包含当前范围外账户，不能继续展示'})
        portfolio={'plan':saved,'snapshot':snapshot,
            'notice':'准备交易时的整体方案和历史快照；仅形成调整建议，交还后须重读当前资料并由本人重新校验'}
        # operator_facts releases the read transaction and rechecks the task under fresh state.
        row=await db.get(WealthTaskORM,task_id,populate_existing=True)
        current_plan=await db.get(WealthPlanORM,plan_view['plan_id'],populate_existing=True)
        if (not current_plan or current_plan.version!=plan_view['version']
                or current_plan.user_id!=row.user_id
                or current_plan.data.get('portfolio_snapshot_id')!=snapshot['snapshot_id']
                or current_plan.data.get('portfolio_version')!=saved['version']):
            raise HTTPException(409,detail={'code':'CONTEXT_MISMATCH','message':'读取期间整体方案版本已变化，请重新核对'})
    return {'task':await tasks.view(db,row), 'conversation':conversation_view,
        'plan':plan_view,
        'portfolio':portfolio,'notice':'只读原任务上下文；操作员不能代用户确认或授权'}


@router.post('/operator/tasks/{task_id}/{action}')
async def operator_control(task_id:str, action:Literal['claim','note','release'], request:TaskControl,
    user:dict=Depends(operator), db:AsyncSession=Depends(get_db)):
    row = await db.get(WealthTaskORM, task_id)
    if not row: raise HTTPException(404, detail={'code':'NOT_FOUND','message':'任务不存在'})
    try: return await tasks.control(db, row.user_id, task_id, action, request.model_dump(), actor_id=user['user_id'])
    except WealthPolicyError as error:
        await db.rollback(); fail(error)
