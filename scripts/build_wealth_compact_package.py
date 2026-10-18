"""Rebind a reduced verified snapshot via the real bank importer, then package.

No existing ledger is opened. Public facts/dates and model parameters stay put.
"""
import asyncio,hashlib,json,re,tempfile,zipfile,shutil,gzip
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from backend.app.models import Base,UserORM,AccountORM
from backend.app.services.bank.wealth_research import import_dataset,bind_shared_account
from backend.app.services.bank.wealth_compact_upgrade import pool
from backend.app.models.wealth_research import WealthContextSelectionORM
from backend.app.services.bank.wealth_service import products
from backend.app.domain.wealth_product_content import product_content
from backend.app.services.orchestration.wealth_portfolio_data import read,save,digest,coverage,canonical
from scripts.wealth_portfolio_package import export

ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/'config/wealth/portfolio_content_binding_v2.json').read_bytes())['data_version']

async def build(root=ROOT):
    selected=pool(root);codes=set(selected['codes'])
    with tempfile.TemporaryDirectory(prefix='compact-bank-') as tmp:
        engine=create_async_engine('sqlite+aiosqlite:///'+str(Path(tmp)/'binding.db'))
        async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine,expire_on_commit=False)() as db:
            db.add(UserORM(user_id='BUILD',name='offline binding',phone='13800000999',id_card='simulation'))
            await db.flush();db.add(AccountORM(account_id='BUILD-A',user_id='BUILD',account_type='savings',card_number='simulation',balance_fen=0,daily_transfer_limit_fen=0));await db.flush()
            payload=json.loads((root/'config/wealth/paper_products.json').read_bytes())
            result=await import_dataset(db,payload)
            if not result['ok']:raise ValueError(result)
            ctx=await bind_shared_account(db,'BUILD','BUILD-A',result['dataset_key'])
            db.add(WealthContextSelectionORM(user_id='BUILD',context_id=ctx['context_id'],version=1));await db.commit()
            rows=await products(db,'BUILD');by_code={r['product_id']:r for r in rows}
            if set(by_code)!=codes:raise ValueError('BANK_POOL_MISMATCH')
        await engine.dispose()
    base_dir=root/'.deps/wealth_portfolio_data/snapshots'
    if not (base_dir/(BASE+'.json')).exists():
        from scripts.install_wealth_offline import PACKAGE_NAME
        from backend.app.schemas.wealth_portfolio_data import DataSnapshot
        with zipfile.ZipFile(root/'data/wealth'/PACKAGE_NAME) as archive:
            source=DataSnapshot.model_validate(json.loads(gzip.decompress(archive.read('package/'+BASE+'.snapshot.json.gz'))))
        save(source,base_dir)
    base=read(root,base_dir,BASE)
    base.products=[p for p in base.products if p.source_kind!='real' or p.product_id in codes]
    base.daily=[d for d in base.daily if d.source_kind!='real' or d.product_id in codes]
    capture_sha=digest(rows)
    for p in base.products:
        if p.source_kind=='real':
            row=by_code[p.product_id];p.product_version=row['version'];p.disclosure_version=row['disclosure']['version']
            p.execution_policy=row['rules']['execution_policy']
            p.current_binding={**p.current_binding,'content_sha256':digest(product_content(row)),'capture_sha256':capture_sha}
    # Drop references belonging only to removed products, including old global
    # inventories. Selected facts carry their own original reference objects.
    refs={}
    def visit(value):
        if isinstance(value,dict):
            if {'path','sha256','function'}<=value.keys():refs[(value['path'],value['sha256'])]=value
            for v in value.values():visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    for p in base.products:visit(p.model_dump(mode='json'))
    for d in base.daily:visit(d.model_dump(mode='json'))
    from backend.app.schemas.wealth_portfolio_data import RawReference
    base.input_references=[RawReference.model_validate(v) for v in refs.values()]
    base.coverage=coverage(base.products,base.daily);base.coverage['compact_pool_version']=selected['version']
    base.fixed_version=digest({'products':[p.model_dump(mode='json') for p in base.products],'pool':selected['version']})
    base.daily_version=digest([d.model_dump(mode='json') for d in base.daily])
    base.evidence_version=digest({'capture':capture_sha,'manifest':hashlib.sha256((root/'config/wealth/portfolio_evidence_manifest.json').read_bytes()).hexdigest()})
    base.version=digest({'fixed':base.fixed_version,'daily':base.daily_version,'evidence':base.evidence_version})
    save(base,root/'.deps/wealth_portfolio_data/snapshots')
    binding=json.loads((root/'config/wealth/portfolio_content_binding_v2.json').read_bytes())
    binding.update(previous_data_version=BASE,data_version=base.version,daily_version=base.daily_version,
        capture_sha256=capture_sha,verified_previous_bindings=selected['count'],pool_version=selected['version'])
    (root/'config/wealth/portfolio_content_binding_v2.json').write_bytes(canonical(binding))
    joint=root/'backend/app/services/orchestration/wealth_portfolio_joint.py'
    text=joint.read_text(encoding='utf-8');text=re.sub(r"DATA='[0-9a-f]{64}'",f"DATA='{base.version}'",text);text=re.sub(r"DAILY='[0-9a-f]{64}'",f"DAILY='{base.daily_version}'",text);joint.write_text(text,encoding='utf-8')
    signature=digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'config/wealth').glob('*.json'))})[:12]
    stage=root/'.deps/compact_upgrade'/('package-'+base.version[:12]+'-'+signature);package=export(root,base.version,stage)
    target=root/'data/wealth'/f"wealth-compact-{selected['count']}-v1.zip";target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(stage.iterdir()):z.write(path,'package/'+path.name)
        for relative in sorted(package['public_files']):z.write(root/relative,relative)
    sha=hashlib.sha256(target.read_bytes()).hexdigest()
    installer=root/'scripts/install_wealth_offline.py';text=installer.read_text(encoding='utf-8')
    for name,value in [('PACKAGE_NAME',target.name),('PACKAGE_SHA',sha),('DATA_VERSION',base.version)]:
        text=re.sub(name+r" = '[^']+'",name+" = '"+value+"'",text)
    installer.write_text(text,encoding='utf-8')
    print(json.dumps({'data_version':base.version,'daily_version':base.daily_version,'products':len(base.products),'real_funds':selected['count'],'zip_bytes':target.stat().st_size,'zip_sha256':sha},ensure_ascii=False))

if __name__=='__main__':asyncio.run(build())
