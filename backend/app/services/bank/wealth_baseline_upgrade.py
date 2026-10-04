"""Explicit administrator upgrade of owned disposable scopes, never startup cleanup."""
from uuid import uuid4
from sqlalchemy import select
from ...models import AccountORM
from ...models.wealth import WealthTaskORM,WealthTaskNodeORM,WealthExecutionORM,WealthProductORM
from ...models.wealth_research import WealthContextORM,WealthContextSelectionORM
from ...domain.time import utc_now,utc_iso
from ...domain.wealth_fixed_data import synthetic_payload,bank_products,DATASET_VERSION
from ...integrations.wealth_authorization import lock_profile
from . import wealth_demo as demo,wealth_research as research
from .transfer_service import BankError


async def fixed_payload(db):
    """Bind new rule versions; keep all old versions used by other scopes intact."""
    payload=synthetic_payload();payload['products']+=bank_products()
    payload.update(dataset_id='semantic-fixed',dataset_version=DATASET_VERSION,mode='snapshot')
    for product in payload['products']:
        old=await db.get(WealthProductORM,product['product_id'])
        version=(old.current_version+1) if old else int(product['product_version'])
        product.update(product_version=version,disclosure_version=version)
        product['rules']={**product['rules'],'dataset_id':payload['dataset_id'],'dataset_version':DATASET_VERSION}
    return payload


async def upgrade(db,user_id,expected_context_id,backup_sha256,payload):
    """Archive through the existing scope barriers, then use guarded physical delete.

    Caller isolates writers and verifies the complete database backup. Unknown
    writes, live leases, mixed history and cross-scope dependencies block cleanup.
    """
    if not demo.enabled() or len(backup_sha256)!=64:raise BankError('缺少已核验备份','BACKUP_REQUIRED',409)
    await lock_profile(db,user_id)
    selection=await db.get(WealthContextSelectionORM,user_id)
    if not selection or selection.context_id!=expected_context_id:raise BankError('当前范围已改变','STALE_CONTEXT',409)
    current=await demo.context_owned(db,user_id,expected_context_id)
    old=(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==user_id))).all()
    old=[ctx for ctx in old if ctx.data.get('disposable') is True]
    for ctx in old:
        deps=await demo.archive_dependencies(db,ctx)
        nodes=(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id.in_(deps['tasks'])))).all()
        executions=(await db.scalars(select(WealthExecutionORM).where(WealthExecutionORM.authorization_id.in_(deps['auths'])))).all()
        if any(n.status in {'RUNNING','UNKNOWN'} or n.lease_until for n in nodes) or any(e.status in {'UNKNOWN','SUBMITTING'} for e in executions):
            raise BankError('旧范围仍有租约或未知结果，须先原号核对','CLEANUP_BLOCKED',409)
        if await demo.cross_scope_blockers(db,ctx,deps):raise BankError('旧范围存在跨范围引用','CLEANUP_BLOCKED',409)
    imported=await research.import_dataset(db,payload)
    if not imported['ok']:raise BankError('新固定数据未通过校验','INVALID_DATASET',422)
    from .wealth_service import lock_accounts
    await lock_accounts(db,[ctx.account_id for ctx in old])
    now=utc_now()
    for ctx in old:
        await demo.invalidate(db,user_id,ctx.account_id)
        if ctx.data.get('run_state','ACTIVE')!='ARCHIVED':
            ctx.version+=1
            ctx.data={**ctx.data,'run_state':'ARCHIVED','archived_at':utc_iso(now),'backup_sha256':backup_sha256}
    tasks=(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.account_id.in_([c.account_id for c in old])))).all()
    for task in tasks:
        if task.status not in {'COMPLETED','PARTIAL_COMPLETED','CANCELLED','FAILED'}:
            task.status='PAUSED';task.waiting_reason='ARCHIVED_RUN';task.version+=1
    rid='RUN'+uuid4().hex[:28];aid='SA'+uuid4().hex[:28]
    db.add(AccountORM(account_id=aid,user_id=user_id,account_type='savings',card_number='模拟测试****'+aid[-4:],
        balance_fen=2000000,daily_transfer_limit_fen=5000000,daily_transfer_used_fen=0,status='active'))
    await db.flush()
    fresh=WealthContextORM(context_id=rid,user_id=user_id,account_id=aid,dataset_key=imported['dataset_key'],version=1,
        data={'label':'固定行情｜20,000元空持仓基线','mode':'snapshot','scenario':'empty','fixture':{},
            'disposable':True,'run_state':'ACTIVE','business_at':utc_iso(now),'run_id':rid,'backup_sha256':backup_sha256})
    db.add(fresh);await db.flush()
    from .wealth_fixture_baseline import freeze
    await freeze(db,fresh)
    fresh.data={**fresh.data,'fixture_ready':True}
    selection.context_id=rid;selection.version+=1
    # Archive boundary is persisted before physical deletion; failed deletion
    # leaves old rounds archived and recoverable from the verified full backup.
    await db.commit()
    runs=[{'run_id':ctx.context_id,'expected_version':ctx.version} for ctx in old]
    preview=await demo.deletion_preview(db,user_id,runs)
    deleted=await demo.write(db,user_id,'delete',{'request_id':'semantic-cleanup-'+rid,
        'runs':runs,'confirmed':True,'preview_digest':preview['preview_digest']})
    return {'run_id':rid,'account_id':aid,'dataset_key':imported['dataset_key'],
        'balance_fen':2000000,'opening_fixture':'empty','business_at':utc_iso(now),
        'backup_sha256':backup_sha256,'deleted':deleted,'product_count':len(payload['products'])}
