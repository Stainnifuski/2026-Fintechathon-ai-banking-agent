"""Idempotent catalogue upgrade on an existing bank ledger; never issues cash.

Historical executed facts are protected. An out-of-pool financial reference
stops the transaction instead of silently deleting the holder's asset/history.
"""
import asyncio,hashlib,json,os,re,sqlite3,subprocess,tempfile,shutil
from functools import lru_cache
from pathlib import Path
from sqlalchemy import select,delete,text
from ...models import Base,AccountORM
from ...models.wealth import WealthProductORM,WealthProductVersionORM,WealthNavORM,WealthOrderORM,WealthLotORM
from ...models.wealth_research import (WealthContextORM,WealthContextSelectionORM,WealthDatasetORM,
    WealthResearchProductORM,WealthFundDirectoryORM,WealthFundCacheORM,WealthKnowledgeORM)
from .wealth_research import import_dataset,bind_shared_account

ROOT=Path(__file__).resolve().parents[4]


@lru_cache(maxsize=4)
def pool(root=ROOT):
    return json.loads((root/'config/wealth/compact_pool.json').read_bytes())


async def prepare_deployment(database_url,root=ROOT):
    """Persistent pre-schema backup, then verified packaged material install.

    This is run before create_all/seed. Never starts an old DB container or
    switches a DATABASE_URL. Passwords are passed only through the child env.
    """
    from sqlalchemy.engine import make_url
    from scripts.install_wealth_offline import install_release,DATA_VERSION,PACKAGE_NAME
    url=make_url(database_url);manifest=pool(root)
    backups=root/'.deps/upgrade_backups';backups.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(str(url.set(password=None)).encode()).hexdigest()[:12]
    dest=backups/(manifest['version']+'-'+identity+('.db' if url.drivername.startswith('sqlite') else '.dump'))
    if not dest.exists():
        tmp=dest.with_suffix(dest.suffix+'.partial')
        def backup():
            if url.drivername.startswith('sqlite'):
                source=Path(url.database)
                if not source.exists():return
                # SQLite journal/page IO stays on the native DB filesystem;
                # only the finished consistent file is copied to a host bind.
                with tempfile.TemporaryDirectory(prefix='bank-backup-',dir=source.parent) as local:
                    native=Path(local)/'backup.db'
                    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(native) as out:src.backup(out)
                    shutil.copyfile(native,tmp)
            elif url.drivername.startswith('postgresql'):
                env={**os.environ,'PGPASSWORD':url.password or ''}
                args=['pg_dump','-h',url.host or 'localhost','-p',str(url.port or 5432),'-U',url.username or 'postgres','-d',url.database,'-Fc','-f',str(tmp)]
                completed=subprocess.run(args,env=env,capture_output=True)
                if completed.returncode:raise RuntimeError('PostgreSQL升级前备份失败，未进行结构或目录升级，请检查数据库连接与pg_dump版本')
            else:raise ValueError('UNSUPPORTED_UPGRADE_DATABASE')
            if tmp.exists():tmp.replace(dest)
        await asyncio.to_thread(backup)
    await asyncio.to_thread(install_release,root,root/'data/wealth'/PACKAGE_NAME)


async def protected_digest(db):
    """All existing non-catalogue, non-context rows, including teammate modules."""
    result={}
    ignored={'wealth_datasets','wealth_research_products','wealth_products','wealth_product_versions','wealth_navs',
        'wealth_contexts','wealth_context_selections','wealth_fund_directory','wealth_fund_cache','wealth_knowledge',
        # Audit tables append legitimate upgrade observations; they are guarded
        # by the append-only writer and must not be compared as a fixed ledger.
        'wealth_schema_versions','audit_logs','bank_audit_events','audit_events','audit_event_objects'}
    for table in Base.metadata.sorted_tables:
        if table.name in ignored:continue
        rows=(await db.execute(select(table).order_by(*table.primary_key.columns))).mappings().all()
        result[table.name]={str(tuple(r[k.name] for k in table.primary_key.columns)):
            hashlib.sha256(json.dumps(dict(r),sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest() for r in rows}
    return result


async def upgrade(db,root=ROOT):
    manifest=pool(root);allowed=set(manifest['codes'])
    if db.get_bind().dialect.name=='postgresql':
        await db.execute(text("SELECT pg_advisory_xact_lock(780190017)"))
    newer=(await db.scalars(select(WealthProductORM.product_id).where(
        WealthProductORM.product_id.in_(sorted(allowed)),WealthProductORM.current_version>manifest['product_version']))).all()
    if newer:raise ValueError('已有基金规则版本比部署资料更新，禁止降级；请核对：'+','.join(sorted(newer)))
    removed=[p for p in (await db.scalars(select(WealthProductORM.product_id))).all()
        if re.fullmatch(r'\d{6}',p) and p not in allowed]
    # Check before importing or modifying any context. Never delete trade facts.
    referenced=set()
    for cls in (WealthOrderORM,WealthLotORM):
        referenced.update(v.split(':')[0] for v in (await db.scalars(select(cls.version_id))).all())
    conflict=sorted(set(removed)&referenced)
    if conflict:raise ValueError('缩减已停止：池外基金仍有成交或持仓记录，须先核定保留范围，未删除账务：'+','.join(conflict))
    before=await protected_digest(db)
    payload=json.loads((root/'config/wealth/paper_products.json').read_bytes())
    supplement=json.loads((root/'config/wealth/portfolio_bank_supplement.json').read_bytes())
    supplement.pop('policy_version',None)
    for value in (payload,supplement):
        result=await import_dataset(db,value)
        if not result['ok']:raise ValueError('CATALOG_IMPORT_INVALID:'+str(result['errors'][:3]))
    directory=set(await db.scalars(select(WealthFundDirectoryORM.code)))
    for p in payload['products']:
        if p['product_id'] not in directory:
            db.add(WealthFundDirectoryORM(code=p['product_id'],name=p['name'],
                fund_type={'bond':'债券型','mixed':'混合型','equity':'股票型'}[p['category']],
                metadata_json={'source':'fixed_public_catalogue','source_product_code':p['product_id'],
                    'classification_basis':'verified_project_category','pool_version':manifest['version'],
                    'live_refresh':False}))
    key=manifest['dataset_key'];additional=supplement['dataset_id']+':'+supplement['dataset_version']
    contexts=(await db.scalars(select(WealthContextORM))).all()
    real_contexts=[c for c in contexts if c.dataset_key.startswith('real-public-paper-v1:')]
    for ctx in real_contexts:
        if ctx.dataset_key!=key or additional not in ctx.data.get('additional_dataset_keys',[]):
            ctx.dataset_key=key;ctx.version+=1
            ctx.data={**ctx.data,'additional_dataset_keys':list(dict.fromkeys([*ctx.data.get('additional_dataset_keys',[]),additional])),
                'pool_revision':manifest['version']}
    # Existing ordinary bank accounts remain the source of funds; no synthetic
    # user, account, risk assessment or MFA grant is created.
    bound=0
    accounts=(await db.scalars(select(AccountORM).where(AccountORM.status=='active',AccountORM.account_type.in_(['savings','checking'])).order_by(AccountORM.account_id))).all()
    for account in accounts:
        ctx=next((c for c in contexts if c.account_id==account.account_id),None)
        if ctx is None:
            result=await bind_shared_account(db,account.user_id,account.account_id,key)
            ctx=await db.get(WealthContextORM,result['context_id']);contexts.append(ctx);bound+=1
            ctx.data={**ctx.data,'additional_dataset_keys':[additional],'pool_revision':manifest['version']}
        # Preserve explicit isolated/archived contexts, rather than silently
        # converting old test money into a normal bank account.
        current=await db.get(WealthContextSelectionORM,account.user_id)
        if ctx.data.get('account_scope')=='shared_bank' and (current is None or current.context_id=='legacy-v1'):
            if current:current.context_id=ctx.context_id;current.version+=1
            else:db.add(WealthContextSelectionORM(user_id=account.user_id,context_id=ctx.context_id,version=1))
    # Remove out-of-pool material in dependency order on BOTH supported engines.
    # Chunk lists for SQLite variable limits; all chunks remain one transaction.
    for offset in range(0,len(removed),300):
        ids=removed[offset:offset+300]
        vids=select(WealthProductVersionORM.version_id).where(WealthProductVersionORM.product_id.in_(ids))
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id.in_(vids)))
        await db.execute(delete(WealthResearchProductORM).where(WealthResearchProductORM.product_id.in_(ids)))
        await db.execute(delete(WealthProductVersionORM).where(WealthProductVersionORM.product_id.in_(ids)))
        await db.execute(delete(WealthProductORM).where(WealthProductORM.product_id.in_(ids)))
    await db.execute(delete(WealthFundDirectoryORM).where(WealthFundDirectoryORM.code.not_in(sorted(allowed))))
    await db.execute(delete(WealthFundCacheORM).where(WealthFundCacheORM.code.not_in(sorted(allowed))))
    # Research-only entries need not have a bank product row.
    records=(await db.scalars(select(WealthResearchProductORM))).all()
    for row in records:
        if re.fullmatch(r'R?\d{6}',row.product_id) and row.product_id.removeprefix('R') not in allowed:await db.delete(row)
    for row in (await db.scalars(select(WealthKnowledgeORM))).all():
        codes=row.metadata_json.get('product_ids',[])
        if any(re.fullmatch(r'R?\d{6}',str(c)) and str(c).removeprefix('R') not in allowed for c in codes):await db.delete(row)
    await db.flush()
    after=await protected_digest(db)
    for table,rows in before.items():
        preserved=all(after[table].get(k)==v for k,v in rows.items())
        if not preserved or (table!='wealth_profiles' and rows!=after[table]):
            raise ValueError('升级保护校验失败：'+table+'账务或其他场景数据改变，事务必须回滚')
    return {'pool_version':manifest['version'],'real_funds':manifest['count'],'categories':manifest['categories'],
        'removed_products':len(removed),'bound_existing_accounts':bound,'financial_and_teammate_rows_unchanged':True}
