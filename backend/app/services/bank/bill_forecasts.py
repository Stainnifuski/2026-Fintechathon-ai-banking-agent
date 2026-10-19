"""Saved baseline predictions and time-bounded actual flow comparisons."""
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select,not_,and_
from ...models.bill_features import BillForecastSnapshotORM as Snapshot
from ...models.transaction import TransactionORM as Tx
from ...models.bill_foundation import BillTransactionMetaORM as Meta
from .bill_foundation import utc,account_ids
from .transfer_service import BankError


async def actual(db,user_id,snapshot,now):
    eligible=set(await account_ids(db,user_id));saved=set(snapshot.payload['account_ids'])
    if not saved<=eligible:raise BankError('预测账户授权范围已变化','FORECAST_SCOPE_CHANGED',409)
    end=min(utc(now),utc(snapshot.end_at))
    rows=(await db.scalars(select(Tx).where(Tx.account_id.in_(saved),Tx.status=='completed',Tx.timestamp>=utc(snapshot.start_at),Tx.timestamp<end,Tx.type.in_(('purchase','subscription','salary','refund','transfer_in','transfer_out'))))).all()
    if snapshot.account_id is None:rows=[t for t in rows if not(t.type in ('transfer_in','transfer_out') and t.counterparty in saved)]
    incoming=sum(t.amount_fen for t in rows if t.type in ('salary','refund','transfer_in'))
    outgoing=sum(t.amount_fen for t in rows if t.type in ('purchase','subscription','transfer_out'))
    fixed={tuple(pair) for pair in snapshot.payload.get('fixed_merchants',[])}
    entries=snapshot.payload.get('fixed_entries')
    metas={m.tx_id:m for m in (await db.scalars(select(Meta).where(Meta.tx_id.in_([t.tx_id for t in rows])))).all()}
    def is_fixed(t):
        pair=(t.account_id,metas[t.tx_id].merchant_id if t.tx_id in metas else None)
        if entries is None:return pair in fixed
        return any(pair==(item['account_id'],item['merchant_id']) and abs(t.amount_fen-item['amount_fen'])<=item['tolerance_fen'] for item in entries)
    variable=[t for t in rows if t.type=='purchase' and not is_fixed(t)]
    return {'inflow_fen':incoming,'outflow_fen':outgoing,'net_flow_fen':incoming-outgoing,'variable_spending_fen':sum(t.amount_fen for t in variable),'variable_count':len(variable)}


async def save(db,user_id,request,*,now=None):
    from .bill_cashflow import project_cashflow
    if request.reductions or request.excluded_subscription_ids or request.one_off_events:
        raise BankError('请恢复基准后保存，假设情景不能作为实际校准依据','BASELINE_REQUIRED',422)
    result=await project_cashflow(db,user_id,request,now=now)
    from ...models.account import AccountORM
    ids=list((await db.scalars(select(AccountORM.account_id).where(AccountORM.account_id.in_(await account_ids(db,user_id)),AccountORM.account_type=='savings',AccountORM.status=='active',*([AccountORM.account_id==request.account_id] if request.account_id else [])))).all())
    if not ids:raise BankError('没有可保存的储蓄账户','CASH_ACCOUNT_REQUIRED',422)
    from ...models.bill_foundation import BillRecurringStreamORM as Stream
    fixed=[list(pair) for pair in (await db.execute(select(Stream.account_id,Stream.merchant_id).where(Stream.user_id==user_id,Stream.account_id.in_(ids),Stream.kind=='expense',Stream.status=='ACTIVE',Stream.enabled.is_(True)))).all()]
    entries=[{'account_id':s.account_id,'merchant_id':s.merchant_id,'amount_fen':s.amount_fen,'tolerance_fen':s.amount_fen//5 if s.source=='detected' else 0} for s in (await db.scalars(select(Stream).where(Stream.user_id==user_id,Stream.account_id.in_(ids),Stream.kind=='expense',Stream.status=='ACTIVE',Stream.enabled.is_(True)))).all()]
    events=[e for e in result['events'] if e['source']!='scenario']
    inflow=sum(e['amount_fen'] for e in events if e['amount_fen']>0)
    outflow=sum(-e['amount_fen'] for e in events if e['amount_fen']<0)+result['estimated_variable_spending_fen']
    payload={'account_ids':ids,'fixed_merchants':fixed,'fixed_entries':entries,'variable_fen':result['estimated_variable_spending_fen'],'calibration':result.get('calibration',{}),'events':[{**e,'at':e['at'].isoformat()} for e in events]}
    row=Snapshot(snapshot_id='F-'+uuid4().hex,user_id=user_id,account_id=request.account_id,start_at=utc(result['as_of']),end_at=utc(result['end_exclusive']),opening_fen=result['opening_available_fen'],predicted_inflow_fen=inflow,predicted_outflow_fen=outflow,payload=payload)
    db.add(row);await db.flush();return {'snapshot_id':row.snapshot_id,'start_at':row.start_at,'end_at':row.end_at}


async def comparisons(db,user_id,*,now=None):
    now=utc(now or datetime.now(timezone.utc));items=[]
    rows=(await db.scalars(select(Snapshot).where(Snapshot.user_id==user_id).order_by(Snapshot.start_at.desc()).limit(30))).all()
    for row in rows:
        try:observed=await actual(db,user_id,row,now)
        except BankError:
            items.append({'snapshot_id':row.snapshot_id,'scope_changed':True});continue
        complete=now>=utc(row.end_at)
        predicted=row.predicted_inflow_fen-row.predicted_outflow_fen
        items.append(dict(snapshot_id=row.snapshot_id,scope_changed=False,account_id=row.account_id,start_at=utc(row.start_at),end_at=utc(row.end_at),complete=complete,
            predicted_net_flow_fen=predicted,actual_net_flow_fen=observed['net_flow_fen'],actual_variable_spending_fen=observed['variable_spending_fen'],predicted_variable_spending_fen=row.payload['variable_fen'],
            error_fen=observed['net_flow_fen']-predicted if complete else None))
    return {'items':items,'as_of':now,'notices':['仅比较保存时账户范围内、预测起止时间内的已完成流水；未结束的预测不计算最终误差。','余额可能含冻结和范围变化，因此比较收支净变化，不将当前余额冒充过去期末余额。','校准至少需要3个互不重叠、已结束且有足够实际消费的基准预测期，只调整可变消费估计，系数限0.5–1.5。']}


async def calibration(db,user_id,account_id,now):
    rows=(await db.scalars(select(Snapshot).where(Snapshot.user_id==user_id,Snapshot.account_id==account_id,Snapshot.end_at<=utc(now)).order_by(Snapshot.end_at.desc()).limit(30))).all()
    chosen=[];latest_start=None;expected=observed=0
    for row in rows:
        if row.payload.get('variable_fen',0)<=0 or (latest_start and utc(row.end_at)>latest_start):continue
        try:actuals=await actual(db,user_id,row,now)
        except BankError:continue
        if actuals['variable_count']<5:continue
        # Store raw estimate to avoid multiplying an already calibrated factor on every generation.
        previous_factor=Decimal(str(row.payload.get('calibration',{}).get('factor',1)))
        expected+=Decimal(row.payload['variable_fen'])/previous_factor;observed+=actuals['variable_spending_fen']
        chosen.append(row.snapshot_id);latest_start=utc(row.start_at)
        if len(chosen)==5:break
    factor=max(Decimal('.5'),min(Decimal('1.5'),Decimal(observed)/expected)) if len(chosen)>=3 and expected else Decimal(1)
    return {'factor':float(factor),'sample_periods':len(chosen),'snapshot_ids':chosen,'applied':len(chosen)>=3,
            'reason':'已结束且互不重叠的预测期支出校准' if len(chosen)>=3 else '不足3个独立已结束预测期，沿用历史消费基线'}
