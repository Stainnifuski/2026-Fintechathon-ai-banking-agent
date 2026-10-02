"""管理员显式数据工具；受控文件内容导入，默认不联网、不改原账本。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from ...models import Base
from ...models.wealth_research import WealthDatasetORM, WealthSyncJobORM, WealthMacroORM
from . import wealth_research as research


async def sync(url: str, report: dict) -> dict:
    engine=create_async_engine(url)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as db:
        result=await research.import_dataset(db,report["dataset"])
        if result["ok"]:
            for row in report["macros"]:
                rid=result["dataset_key"]+":"+row["indicator"]
                if await db.get(WealthMacroORM,rid) is None: db.add(WealthMacroORM(record_id=rid,dataset_key=result["dataset_key"],indicator=row["indicator"],observations=row["observations"]))
        await db.commit()
    await engine.dispose()
    return result


async def claim_job(url: str,job_id: str) -> bool:
    """唯一主键及条件更新跨进程抢占；过期租约允许重启恢复。"""
    from ...models.wealth_research import WealthSyncJobORM
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    engine=create_async_engine(url)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as db:
        now=datetime.now(timezone.utc); lease=now+timedelta(minutes=40)
        insert=sqlite_insert if db.get_bind().dialect.name=="sqlite" else pg_insert
        r=await db.execute(insert(WealthSyncJobORM).values(job_id=job_id,status="RUNNING",started_at=now,lease_until=lease,result={})
            .on_conflict_do_nothing(index_elements=["job_id"]))
        acquired=r.rowcount==1
        if not acquired:
            r=await db.execute(update(WealthSyncJobORM).where(WealthSyncJobORM.job_id==job_id,WealthSyncJobORM.lease_until<now,WealthSyncJobORM.status!="SUCCEEDED")
                .values(status="RUNNING",started_at=now,lease_until=lease))
            acquired=r.rowcount==1
        await db.commit()
    await engine.dispose(); return acquired


async def finish_job(url: str,job_id: str,result: dict) -> None:
    engine=create_async_engine(url); sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as db:
        await db.execute(update(WealthSyncJobORM).where(WealthSyncJobORM.job_id==job_id).values(status="SUCCEEDED" if result.get("ok") else "FAILED",result=result,lease_until=datetime.now(timezone.utc)))
        await db.commit()
    await engine.dispose()


async def run(args):
    url=os.getenv("DATABASE_URL")
    if not url: raise ValueError("DATABASE_URL_REQUIRED")
    if args.action in {"probe","sync","worker"}:
        from .wealth_provider import AKShareProvider,probe
        if not args.live: raise ValueError("EXPLICIT_LIVE_REQUIRED")
        if args.action=="worker" and os.getenv("WEALTH_MARKET_SYNC_ENABLED")!="true": raise ValueError("SYNC_DISABLED")
        while True:
            job_id="funds-macro-"+datetime.now(timezone.utc).date().isoformat()
            if args.action=="probe" or await claim_job(url,job_id):
                report=await asyncio.to_thread(probe,AKShareProvider(timeout=args.timeout,retries=1 if args.action=="probe" else 3))
                output=Path(args.output).resolve()
                if output.is_symlink(): raise ValueError("OUTPUT_SYMLINK_DENIED")
                output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(report,ensure_ascii=False),encoding="utf-8")
                result=await sync(url,report) if args.action!="probe" else {"ok":True,"probe_only":True}
                if args.action!="probe": await finish_job(url,job_id,result)
                print(json.dumps({"result":result,"source_results":[{k:v for k,v in r.items() if k!="records"} for r in report["report"]]},ensure_ascii=False))
            else: print(json.dumps({"job_id":job_id,"status":"ALREADY_CLAIMED_OR_SUCCEEDED"}))
            if args.action!="worker": break
            await asyncio.sleep(max(60,args.interval))
        return
    engine=create_async_engine(url)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as db:
        if args.action=='scenarios':
            from .wealth_fixtures import initialize_all
            result=await initialize_all(db)
        elif args.action=="init":
            result=await research.initialize(db,args.user_id,args.scenario,0 if args.scenario=="insufficient_cash" else 2000000)
        elif args.action=="import":
            from .wealth_provider import parse_import
            root=Path(args.import_root).resolve(); path=(root/args.file).resolve()
            if not path.is_relative_to(root) or path.is_symlink() or not path.is_file(): raise ValueError("CONTROLLED_IMPORT_PATH_REQUIRED")
            if path.stat().st_size>research.MAX_IMPORT_BYTES: raise ValueError("INPUT_TOO_LARGE")
            result=await research.import_dataset(db,parse_import(path.read_bytes(),path.suffix.lstrip(".")),args.dry_run)
        else:
            from ...models.wealth_research import WealthContextORM
            row=await db.get(WealthContextORM,args.context_id,with_for_update=True)
            if row is None or not row.data.get("disposable"): raise ValueError("SCENARIO_CONTEXT_REQUIRED")
            if args.action=="clock":
                at=datetime.fromisoformat(args.business_at)
                if at.tzinfo is None or at<datetime.fromisoformat(row.data["business_at"]): raise ValueError("CLOCK_MUST_ADVANCE")
                row.data={**row.data,"business_at":at.isoformat()}; result={"context_id":row.context_id,"business_at":at.isoformat()}
            elif args.action=="reset":
                from ...models.wealth import WealthOrderORM,WealthLotORM
                pending=await db.scalar(select(WealthOrderORM.operation_id).where(WealthOrderORM.account_id==row.account_id,WealthOrderORM.status.not_in(["CANCELLED","SETTLED","REJECTED"])))
                holdings=await db.scalar(select(WealthLotORM.lot_id).where(WealthLotORM.account_id==row.account_id,WealthLotORM.units_scaled>0))
                if pending or holdings: raise ValueError("RESET_REFUSED_OPEN_ORDERS_OR_HOLDINGS")
                if not args.confirm_reset: raise ValueError("RESET_EXPLICIT_CONFIRMATION_REQUIRED")
                from ...models.account import AccountORM
                account=await db.get(AccountORM,row.account_id); before=account.balance_fen; account.balance_fen=2000000; row.version+=1
                from .audit_service import record_bank_event
                record_bank_event(db,event_key=f'{row.context_id}:reset:{row.version}',subject_user_id=row.user_id,action='investment_sandbox_reset',result='success',facts={'context_id':row.context_id,'previous_cash_fen':before,'issued_cash_fen':2000000,'history_retained':True},decision_chain=['explicit_disposable_context','no_open_orders_or_holdings','audit_retained'])
                result={"context_id":row.context_id,"account_id":row.account_id,"reset":True,"audit_retained":True}
            else: raise ValueError("UNKNOWN_ACTION")
        if not args.dry_run: await db.commit()
        else: await db.rollback()
        print(json.dumps(result,ensure_ascii=False))
    await engine.dispose()


def main():
    p=argparse.ArgumentParser(); p.add_argument("action",choices=["init","scenarios","import","probe","sync","worker","clock","reset"])
    p.add_argument("--user-id",default="U001"); p.add_argument("--scenario",default="normal"); p.add_argument("--context-id"); p.add_argument("--business-at")
    p.add_argument("--confirm-reset",action="store_true"); p.add_argument("--import-root",default=".deps/wealth-import"); p.add_argument("--file")
    p.add_argument("--dry-run",action="store_true"); p.add_argument("--live",action="store_true"); p.add_argument("--timeout",type=int,default=35)
    p.add_argument("--interval",type=int,default=3600); p.add_argument("--output",default=".deps/wealth-market/latest-probe.json")
    args=p.parse_args(); asyncio.run(run(args))


if __name__=="__main__": main()
