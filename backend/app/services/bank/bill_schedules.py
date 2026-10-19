"""Calendar recurrence always shifts the original anchor (no month-end drift)."""
import calendar
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from sqlalchemy import select
from ...models.bill_features import BillRecurringScheduleORM as Schedule
from ...models.bill_foundation import BillRecurringStreamORM as Stream,BillMetadataEventORM as Event
from .bill_foundation import lock_users,account_ids,merchant_registry,utc
from .transfer_service import BankError
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Asia/Shanghai')


def occurrences(anchor,frequency,start,end,*,month_end=False):
    anchor=utc(anchor).astimezone(TZ);start=utc(start).astimezone(TZ);end=utc(end).astimezone(TZ)
    if frequency=='WEEKLY':
        offset=max(0,int((start-anchor).total_seconds()//(7*86400)))
        at=anchor+timedelta(weeks=offset)
        while at<end:
            if at>=start:yield at
            at+=timedelta(weeks=1)
        return
    step={'MONTHLY':1,'QUARTERLY':3,'YEARLY':12}[frequency]
    offset=max(0,((start.year-anchor.year)*12+start.month-anchor.month)//step)
    while True:
        year,index=divmod(anchor.year*12+anchor.month-1+offset*step,12);month=index+1
        last=calendar.monthrange(year,month)[1]
        at=anchor.replace(year=year,month=month,day=last if month_end else min(anchor.day,last))
        if at>=end:return
        if at>=start:yield at
        offset+=1


async def save_plan(db,user_id,body,stream_id=None):
    if body.frequency=='WEEKLY' and body.month_end:raise BankError('每周计划不能同时固定月末','INVALID_RECURRENCE',422)
    await lock_users(db,[user_id])
    if body.account_id not in await account_ids(db,user_id):raise BankError('账户不存在','ACCOUNT_NOT_FOUND',404)
    from ...models.account import AccountORM
    account=await db.get(AccountORM,body.account_id)
    if account.account_type!='savings' or account.status!='active':raise BankError('周期计划需有效储蓄账户','CASH_ACCOUNT_REQUIRED',422)
    merchant=await merchant_registry(db,user_id,body.name)
    if merchant is None:raise BankError('名称不能为空白','INVALID_NAME',422)
    row=await db.get(Stream,stream_id,populate_existing=True) if stream_id else None
    if stream_id and (not row or row.user_id!=user_id):raise BankError('周期计划不存在','RECURRING_NOT_FOUND',404)
    if row and row.source!='manual':raise BankError('仅手动计划可以编辑频率与金额','RECURRING_NOT_MANUAL',409)
    if (row.version if row else 0)!=body.expected_version:raise BankError('周期记录已更新','STALE_RECURRING',409)
    duplicates=(await db.scalars(select(Stream).where(Stream.user_id==user_id,Stream.account_id==body.account_id,Stream.merchant_id==merchant.merchant_id,Stream.kind==body.kind,Stream.status=='ACTIVE'))).all()
    for other in duplicates:
        if other.stream_id==stream_id:continue
        if body.active and other.source in ('manual','subscription'):raise BankError('同账户商户已有同方向周期计划，请编辑已有计划','DUPLICATE_RECURRING_PLAN',409)
        if body.active and other.source=='detected':other.enabled=False;other.version+=1
    anchor=utc(body.anchor_at)
    values=dict(user_id=user_id,account_id=body.account_id,merchant_id=merchant.merchant_id,kind=body.kind,source='manual',frequency=body.frequency,anchor_day=anchor.astimezone(TZ).day,month_end=body.month_end,amount_fen=body.amount_fen,status='ACTIVE' if body.active else 'CANCELLED',enabled=body.enabled,next_expected_at=anchor)
    if row is None:row=Stream(stream_id='R-'+uuid4().hex,version=1,**values);db.add(row)
    else:
        for field,value in values.items():setattr(row,field,value)
        row.version+=1
    await db.flush();schedule=await db.get(Schedule,row.stream_id)
    if schedule:schedule.anchor_at=anchor;schedule.category=body.category
    else:db.add(Schedule(stream_id=row.stream_id,anchor_at=anchor,category=body.category))
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='recurring_plan',subject_id=row.stream_id,before={},after={**body.model_dump(mode='json'),'stream_id':row.stream_id}))
    await db.flush();return {'stream_id':row.stream_id,'version':row.version}
