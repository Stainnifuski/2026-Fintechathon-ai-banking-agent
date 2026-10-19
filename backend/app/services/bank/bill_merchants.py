"""Previewed merchant changes, reversible snapshots and explicit owned splits."""
import hashlib,json
from uuid import uuid4
from sqlalchemy import select,update
from ...models.bill_foundation import BillMerchantORM as Merchant,BillMerchantAliasORM as Alias,BillTransactionMetaORM as Meta,BillMetadataEventORM as Event,BillRecurringStreamORM as Stream
from ...models.bill_features import BillCategoryRuleORM as Rule
from .bill_foundation import lock_users,account_ids,normalize,key
from .transfer_service import BankError


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


async def state(db,user_id,merchant_id,*,active=True):
    merchant=await db.get(Merchant,merchant_id,populate_existing=True)
    if not merchant or merchant.user_id!=user_id or (active and merchant.merged_into_id):raise BankError('商户不存在或无权访问','MERCHANT_NOT_FOUND',404)
    aliases=list((await db.scalars(select(Alias.alias_id).where(Alias.user_id==user_id,Alias.merchant_id==merchant_id).order_by(Alias.alias_id))).all())
    from ...models.transaction import TransactionORM as Tx
    tx_ids=list((await db.scalars(select(Meta.tx_id).join(Tx,Tx.tx_id==Meta.tx_id).where(Meta.merchant_id==merchant_id,Tx.account_id.in_(await account_ids(db,user_id))).order_by(Meta.tx_id))).all())
    rule=await db.get(Rule,merchant_id)
    return {'merchant_id':merchant_id,'name':merchant.display_name,'bank_identity':merchant.bank_identity,'merged_into_id':merchant.merged_into_id,'alias_ids':aliases,'tx_ids':tx_ids,'rule_version':rule.version if rule else 0}


async def preview(db,user_id,target_id,source_id):
    target=await state(db,user_id,target_id);source=await state(db,user_id,source_id)
    allowed=target_id!=source_id and not(target['bank_identity'] and source['bank_identity'] and target['bank_identity']!=source['bank_identity'])
    return {'target':target['name'],'source':source['name'],'transaction_count':len(source['tx_ids']),'alias_count':len(source['alias_ids']),
        'allowed':allowed,'preview_digest':digest([target,source]),'source_merchant_id':source_id,
        'notices':['保留商户的分类规则将应用于无单笔修正的归入流水。','识别出的周期模式会重新计算；新增证据可能使模式失效。','可在归并记录中撤销；若之后商户结构已变化，请使用拆分处理。']}


async def merge(db,user_id,target_id,source_id,expected=None):
    from .bill_foundation import _sync_user
    await lock_users(db,[user_id]);target=await state(db,user_id,target_id);source=await state(db,user_id,source_id)
    if target_id==source_id:return {'merchant_id':target_id}
    if expected and expected!=digest([target,source]):raise BankError('归并范围已变化，请重新预览','MERGE_PREVIEW_CHANGED',409)
    if target['bank_identity'] and source['bank_identity'] and target['bank_identity']!=source['bank_identity']:raise BankError('不同银行商户身份不能归并','MERCHANT_IDENTITY_CONFLICT',409)
    streams=[dict(stream_id=s.stream_id,status=s.status,enabled=s.enabled) for s in (await db.scalars(select(Stream).where(Stream.user_id==user_id,Stream.merchant_id.in_([source_id,target_id]),Stream.source=='detected'))).all()]
    target_row=await db.get(Merchant,target_id);source_row=await db.get(Merchant,source_id)
    target_row.bank_identity=target_row.bank_identity or source_row.bank_identity
    await db.execute(update(Alias).where(Alias.alias_id.in_(source['alias_ids'])).values(merchant_id=target_id))
    await db.execute(update(Meta).where(Meta.tx_id.in_(source['tx_ids'])).values(merchant_id=target_id))
    await db.execute(update(Stream).where(Stream.user_id==user_id,Stream.merchant_id==source_id,Stream.source=='detected').values(status='SUPERSEDED',enabled=False))
    source_row.merged_into_id=target_id;await db.flush();await _sync_user(db,user_id)
    after_target=await state(db,user_id,target_id);after_source=await state(db,user_id,source_id,active=False)
    event_id='E-'+uuid4().hex
    db.add(Event(event_id=event_id,user_id=user_id,action='merchant_merge',subject_id=target_id,before={'target':target,'source':source,'streams':streams},after={'digest':digest([after_target,after_source]),'source_id':source_id,'reversible':True}))
    await db.flush();return {'merchant_id':target_id,'merged_source_id':source_id,'event_id':event_id}


async def history(db,user_id):
    rows=(await db.scalars(select(Event).where(Event.user_id==user_id,Event.action.in_(('merchant_merge','merchant_unmerge','merchant_split'))).order_by(Event.created_at.desc()).limit(50))).all()
    undone={r.after.get('merge_event_id') for r in rows if r.action=='merchant_unmerge'}
    return [dict(event_id=r.event_id,action=r.action,created_at=r.created_at,target_id=r.subject_id,
        target_name=(r.before.get('target') or {}).get('name') if isinstance(r.before.get('target'),dict) else None,source_name=(r.before.get('source') or {}).get('name') if isinstance(r.before.get('source'),dict) else None,
        can_undo=r.action=='merchant_merge' and bool(r.after.get('reversible')) and r.event_id not in undone) for r in rows]


async def undo(db,user_id,event_id):
    from .bill_foundation import _sync_user
    await lock_users(db,[user_id]);event=await db.get(Event,event_id)
    if not event or event.user_id!=user_id or event.action!='merchant_merge':raise BankError('归并记录不存在','MERGE_NOT_FOUND',404)
    if not event.after.get('reversible'):raise BankError('旧归并记录没有还原快照，请按流水拆分','MERGE_SNAPSHOT_UNAVAILABLE',409)
    done=await db.scalar(select(Event.event_id).where(Event.user_id==user_id,Event.action=='merchant_unmerge',Event.subject_id==event_id))
    if done:return {'restored':True,'merge_event_id':event_id}
    target=event.before['target'];source=event.before['source']
    current=[await state(db,user_id,target['merchant_id']),await state(db,user_id,source['merchant_id'],active=False)]
    if digest(current)!=event.after['digest']:raise BankError('归并后商户范围或规则已变化，请使用拆分','MERGE_STATE_CHANGED',409)
    await db.execute(update(Alias).where(Alias.alias_id.in_(source['alias_ids'])).values(merchant_id=source['merchant_id']))
    await db.execute(update(Meta).where(Meta.tx_id.in_(source['tx_ids'])).values(merchant_id=source['merchant_id']))
    (await db.get(Merchant,target['merchant_id'])).bank_identity=target['bank_identity']
    (await db.get(Merchant,source['merchant_id'])).merged_into_id=None
    for saved in event.before['streams']:
        stream=await db.get(Stream,saved['stream_id'])
        if stream:stream.status=saved['status'];stream.enabled=saved['enabled'];stream.version+=1
    await db.flush();await _sync_user(db,user_id)
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='merchant_unmerge',subject_id=event_id,before={},after={'merge_event_id':event_id}))
    await db.flush();return {'restored':True,'merge_event_id':event_id}


async def split_selection(db,user_id,merchant_id,body,snapshot):
    if not normalize(body.name) or len(set(body.tx_ids))!=len(body.tx_ids) or not set(body.tx_ids)<=set(snapshot['tx_ids']):raise BankError('选择的流水不属于当前商户或有重复','INVALID_SPLIT',422)
    selected=set(body.tx_ids)
    metas=(await db.scalars(select(Meta).where(Meta.merchant_id==merchant_id))).all()
    for meta in metas:
        if meta.tx_id in selected and meta.original_tx_id:selected.add(meta.original_tx_id)
    for meta in metas:
        if meta.original_tx_id in selected:selected.add(meta.tx_id)
    aliases=(await db.scalars(select(Alias).where(Alias.user_id==user_id,Alias.merchant_id==merchant_id))).all()
    norms={normalize(name) for name in body.aliases}
    if not norms<={a.normalized_name for a in aliases}:raise BankError('别名不属于当前商户','INVALID_ALIAS',422)
    named_alias=await db.scalar(select(Alias).where(Alias.user_id==user_id,Alias.normalized_name==normalize(body.name)))
    if named_alias and (named_alias.merchant_id!=merchant_id or named_alias.normalized_name not in norms):raise BankError('新名称已有归属，请使用新名称或明确移动该别名','ALIAS_CONFLICT',409)
    return selected,norms


async def protected_transactions(db,tx_ids):
    from ...models.bank_operation import BankOperationORM
    return set((await db.scalars(select(BankOperationORM.bank_tx_id).where(BankOperationORM.bank_tx_id.in_(tx_ids),BankOperationORM.status=='SUCCEEDED'))).all())


async def split_preview(db,user_id,merchant_id,body=None):
    snapshot=await state(db,user_id,merchant_id)
    selected,norms=await split_selection(db,user_id,merchant_id,body,snapshot) if body else (set(),set())
    protected=await protected_transactions(db,selected) if selected else set()
    binding=[snapshot,body.name.strip(),sorted(selected),sorted(norms)] if body else snapshot
    return {'preview_digest':digest(binding),'transaction_count':len(snapshot['tx_ids']),'moving_count':len(selected),'moving_tx_ids':sorted(selected),'allowed':not bool(protected),'protected_tx_ids':sorted(protected),
        'notices':['已将原单和关联退款一起纳入本次拆分；标签与单笔分类保持不变。','拆分后可通过归并重新整理；有银行付款身份依据的流水不能拆成不同身份。']}


async def split(db,user_id,merchant_id,body):
    from .bill_foundation import _sync_user
    await lock_users(db,[user_id]);snapshot=await state(db,user_id,merchant_id)
    selected,norms=await split_selection(db,user_id,merchant_id,body,snapshot)
    if digest([snapshot,body.name.strip(),sorted(selected),sorted(norms)])!=body.preview_digest:raise BankError('拆分范围或参数已变化，请重新预览','MERGE_PREVIEW_CHANGED',409)
    if await protected_transactions(db,selected):raise BankError('所选流水有银行付款身份依据，不能拆成不同身份','VERIFIED_MERCHANT_SPLIT',409)
    merchant=Merchant(merchant_id='M-'+uuid4().hex,user_id=user_id,display_name=body.name.strip());db.add(merchant);await db.flush()
    if not await db.scalar(select(Alias.alias_id).where(Alias.user_id==user_id,Alias.normalized_name==normalize(body.name))):
        db.add(Alias(alias_id=key('A-',user_id+':'+normalize(body.name)),user_id=user_id,name=body.name.strip(),normalized_name=normalize(body.name),merchant_id=merchant.merchant_id))
    await db.execute(update(Meta).where(Meta.tx_id.in_(selected)).values(merchant_id=merchant.merchant_id))
    await db.execute(update(Alias).where(Alias.user_id==user_id,Alias.merchant_id==merchant_id,Alias.normalized_name.in_(norms)).values(merchant_id=merchant.merchant_id))
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='merchant_split',subject_id=merchant_id,before={'tx_ids':sorted(selected)},after={'new_merchant_id':merchant.merchant_id,'aliases':body.aliases}))
    await db.flush();await _sync_user(db,user_id)
    return {'merchant_id':merchant.merchant_id,'transaction_count':len(selected)}
