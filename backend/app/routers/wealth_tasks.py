"""本人任务控制和操作员接管；不接受客户端指定主体或角色。"""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..schemas.wealth import StrictRequest
from ..models.wealth import WealthTaskORM
from ..services.orchestration import wealth_task as tasks
from ..integrations.wealth_authorization import WealthPolicyError
from ..integrations.wealth_bank_api import HttpWealthBankApi
from .wealth import get_wealth_bank

router = APIRouter(prefix='/api/v1/wealth', tags=['wealth-tasks'])


class TaskControl(StrictRequest):
    request_id: str = Field(min_length=8, max_length=64)
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(default='', max_length=1000)


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
    rows = (await db.scalars(select(WealthTaskORM).where(WealthTaskORM.control_mode.in_(['HANDOFF_PENDING','HUMAN'])))).all()
    return {'items':[await tasks.view(db, row) for row in rows]}


@router.get('/operator/tasks/{task_id}/facts')
async def operator_facts(task_id:str, user:dict=Depends(operator), db:AsyncSession=Depends(get_db),
    bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..integrations.bank_api import BankApiError
    row=await db.get(WealthTaskORM,task_id)
    if not row or row.control_mode!='HUMAN' or row.owner_id!=user['user_id']:
        raise HTTPException(403,detail={'code':'HANDOFF_CONFLICT','message':'只有领取操作员可查看处理事实'})
    subject, account_id = row.user_id, row.account_id
    await db.rollback()
    try: return await bank.call('POST','/assets',{'subject_user_id':subject,'account_id':account_id})
    except BankApiError as error: fail(error)


@router.post('/operator/tasks/{task_id}/{action}')
async def operator_control(task_id:str, action:Literal['claim','note','release'], request:TaskControl,
    user:dict=Depends(operator), db:AsyncSession=Depends(get_db)):
    row = await db.get(WealthTaskORM, task_id)
    if not row: raise HTTPException(404, detail={'code':'NOT_FOUND','message':'任务不存在'})
    try: return await tasks.control(db, row.user_id, task_id, action, request.model_dump(), actor_id=user['user_id'])
    except WealthPolicyError as error:
        await db.rollback(); fail(error)
