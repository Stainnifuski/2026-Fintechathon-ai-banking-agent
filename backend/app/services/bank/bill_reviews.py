import hashlib,json
from uuid import uuid4
from sqlalchemy import select
from ...models.bill_features import BillAnomalyReviewORM as Review
from ...models.bill_foundation import BillMetadataEventORM as Event
from .bill_foundation import key,lock_users
from .transfer_service import BankError


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


async def decorate(db,user_id,query,anomalies):
    for item in anomalies:
        identity=[user_id,query.period,query.date,query.account_id,query.category,query.tag_id,item['kind'],sorted(item.get('tx_ids',[])),item.get('category')]
        item['anomaly_id']=key('A-',json.dumps(identity,sort_keys=True))
        item['fingerprint']=fingerprint({k:v for k,v in item.items() if k not in ('anomaly_id','fingerprint')})
    rows={r.anomaly_id:r for r in (await db.scalars(select(Review).where(Review.user_id==user_id,Review.anomaly_id.in_([a['anomaly_id'] for a in anomalies])))).all()}
    for item in anomalies:
        row=rows.get(item['anomaly_id']);current=row and row.fingerprint==item['fingerprint']
        item.update(review_status=row.status if current else 'pending',review_note=row.note if current else '',
            review_version=row.version if row else 0,evidence_changed=bool(row and not current))
    return anomalies


async def save_review(db,user_id,body):
    from .bill_service import analyze,aware
    from datetime import datetime,timezone
    await lock_users(db,[user_id])
    if body.as_of and (body.as_of.tzinfo is None or body.as_of>datetime.now(timezone.utc)):
        raise BankError('截止时间无效','INVALID_AS_OF',422)
    result=await analyze(db,user_id,body.query,now=body.as_of)
    item=next((a for a in result['anomalies'] if a['anomaly_id']==body.anomaly_id),None)
    if not item or item['fingerprint']!=body.fingerprint:raise BankError('异常依据已变化，请刷新核对','ANOMALY_CHANGED',409)
    row=await db.get(Review,body.anomaly_id,populate_existing=True)
    if (row.version if row else 0)!=body.expected_version:raise BankError('核对状态已更新，请刷新','STALE_REVIEW',409)
    before={'status':row.status,'fingerprint':row.fingerprint,'note':row.note} if row else {}
    if row is None:
        row=Review(anomaly_id=body.anomaly_id,user_id=user_id,fingerprint=body.fingerprint,status=body.status,note=body.note,version=1);db.add(row)
    else:row.status=body.status;row.note=body.note;row.fingerprint=body.fingerprint;row.version+=1
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='anomaly_review',subject_id=body.anomaly_id,before=before,after={'status':body.status,'note':body.note,'fingerprint':body.fingerprint}))
    await db.flush();return {'anomaly_id':row.anomaly_id,'status':row.status,'version':row.version}
