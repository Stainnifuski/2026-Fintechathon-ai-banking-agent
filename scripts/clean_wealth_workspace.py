"""Explicit wealth transient-state cleanup. Preview first; financial fingerprints must match.

Run inside the existing backend container. SQLite apply requires a completed backup;
PostgreSQL apply requires --backup pointing to an independently created pg_dump.
"""
import argparse,asyncio,json,hashlib,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from sqlalchemy import select
from sqlalchemy.engine import make_url
from backend.app.database.connection import AsyncSessionLocal,DATABASE_URL
from backend.app.models import AccountORM,TransactionORM,TransferTaskORM,TransferOperationORM,FlowerOrderORM,CakeOrderORM,SubscriptionORM,CardORM,CardOperationORM,CalendarEventORM,PaymentRequestORM
from backend.app.models.wealth import (WealthOrderORM,WealthLotORM,WealthCashHoldORM,
    WealthUnitHoldORM,WealthSettlementORM,WealthNavORM,WealthProductORM,WealthProductVersionORM,WealthAssessmentORM)
from backend.app.models.wealth_research import WealthFundCacheORM,WealthFundDirectoryORM
from backend.app.services.orchestration.wealth_lifecycle import clean_archives


async def fingerprint(db):
    result={}
    for model in (AccountORM,TransactionORM,WealthOrderORM,WealthLotORM,WealthCashHoldORM,
            WealthUnitHoldORM,WealthSettlementORM,WealthNavORM,WealthProductORM,WealthProductVersionORM,
            WealthAssessmentORM,WealthFundCacheORM,WealthFundDirectoryORM,TransferTaskORM,TransferOperationORM,
            FlowerOrderORM,CakeOrderORM,SubscriptionORM,CardORM,CardOperationORM,CalendarEventORM,PaymentRequestORM):
        values=[{c.name:getattr(r,c.name) for c in model.__table__.columns}
            for r in await db.scalars(select(model).order_by(*model.__table__.primary_key.columns))]
        data=json.dumps(values,sort_keys=True,ensure_ascii=False,default=str,separators=(',',':')).encode()
        result[model.__tablename__]={'count':len(values),'sha256':hashlib.sha256(data).hexdigest()}
    return result


async def main(args):
    url=make_url(DATABASE_URL)
    backup=None
    if args.apply:
        if url.get_backend_name()=='sqlite':
            source=Path(url.database).resolve();dest=Path(args.backup_dir).resolve()
            if source.name not in {'wealth-demo.db','banking_agent.db'}:raise RuntimeError('Explicit adopted database required')
            dest.mkdir(parents=True,exist_ok=True)
            backup=dest/('wealth-workspace-before-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.db')
            with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as src,sqlite3.connect(backup) as target:
                src.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('BACKUP_INVALID')
        else:
            backup=Path(args.backup or '').resolve()
            if not backup.is_file() or backup.stat().st_size==0:raise RuntimeError('PG_BACKUP_REQUIRED')
    async with AsyncSessionLocal() as db:
        before=await fingerprint(db)
        result=await clean_archives(db,apply=args.apply)
        after=await fingerprint(db)
        if before!=after:raise RuntimeError('FINANCIAL_FINGERPRINT_CHANGED: restore verified backup and investigate')
        print(json.dumps({'apply':args.apply,'backup':str(backup) if backup else None,
            'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest() if backup else None,
            'cleanup':result,'protected':after,'financial_unchanged':True},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup-dir',default='/demo/backups');parser.add_argument('--backup')
    asyncio.run(main(parser.parse_args()))
