"""One-off explicit local demo upgrade. Stop demo writers first; never use main DB.

python scripts/upgrade_wealth_demo_baseline.py --expected-account <UI account>
    --backup /demo/backups/<verified copy>.db --apply
"""
import argparse,asyncio,hashlib,json,sqlite3
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy import select
from backend.app.database.connection import AsyncSessionLocal,DATABASE_URL
from backend.app.models import AccountORM
from backend.app.models.wealth_research import WealthContextSelectionORM
from backend.app.services.bank.wealth_baseline_upgrade import fixed_payload,upgrade


def protected_snapshot(path,disposable_accounts,disposable_contexts):
    """Compare full protected rows without printing balances, chats or cache data."""
    snapshots={}
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        for table in ['accounts','transactions','wealth_orders','wealth_lots','wealth_assessments',
                      'wealth_fund_cache','wealth_fund_directory','wealth_conversations']:
            rows=[]
            for row in conn.execute('SELECT * FROM '+table):
                value=dict(row)
                if table=='accounts' and value['account_id'] in disposable_accounts:continue
                if table in {'transactions','wealth_orders','wealth_lots'} and value['account_id'] in disposable_accounts:continue
                if table=='wealth_conversations' and json.loads(value['data']).get('context_id') in disposable_contexts:continue
                rows.append(value)
            canonical=json.dumps(sorted(rows,key=lambda r:json.dumps(r,sort_keys=True)),sort_keys=True,separators=(',',':'))
            snapshots[table]={'count':len(rows),'sha256':hashlib.sha256(canonical.encode()).hexdigest()}
    return snapshots


async def main(args):
    url=make_url(DATABASE_URL)
    if url.drivername!='sqlite+aiosqlite' or url.database!='/demo/wealth-demo.db':raise ValueError('LOCAL_DEMO_DATABASE_REQUIRED')
    backup=Path(args.backup).resolve()
    if backup.parent!=Path('/demo/backups') or not backup.is_file():raise ValueError('VERIFIED_DEMO_BACKUP_REQUIRED')
    with sqlite3.connect(str(backup)) as db:
        if db.execute('pragma integrity_check').fetchone()[0]!='ok':raise ValueError('BACKUP_INTEGRITY_FAILED')
    digest=hashlib.sha256(backup.read_bytes()).hexdigest()
    async with AsyncSessionLocal() as db:
        account=await db.get(AccountORM,args.expected_account)
        if not account:raise ValueError('UI_ACCOUNT_NOT_FOUND')
        selection=await db.get(WealthContextSelectionORM,account.user_id)
        from backend.app.models.wealth_research import WealthContextORM
        context=await db.get(WealthContextORM,selection.context_id)
        if context.account_id!=args.expected_account:raise ValueError('SELECTED_ACCOUNT_CHANGED')
        contexts=(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==account.user_id))).all()
        disposable=[c for c in contexts if c.data.get('disposable') is True]
        account_ids={c.account_id for c in disposable};context_ids={c.context_id for c in disposable}
        protected=protected_snapshot(url.database,account_ids,context_ids)
        payload=await fixed_payload(db)
        if not args.apply:
            print(json.dumps({'apply':False,'backup_sha256':digest,'context_id':context.context_id,'products':len(payload['products'])}));return
        result=await upgrade(db,account.user_id,context.context_id,digest,payload)
        # Exclude the new disposable account too; everything outside the authorised
        # old scopes must remain byte-for-byte equivalent at the row level.
        after=protected_snapshot(url.database,account_ids|{result['account_id']},context_ids|{result['run_id']})
        result['protected_rows_unchanged']=after==protected
        result['protected_verification']=after
        if after!=protected:raise ValueError('PROTECTED_SCOPE_CHANGED_RESTORE_FROM_BACKUP')
        report=backup.with_suffix('.upgrade.json');report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--expected-account',required=True)
    parser.add_argument('--backup',required=True);parser.add_argument('--apply',action='store_true')
    asyncio.run(main(parser.parse_args()))
