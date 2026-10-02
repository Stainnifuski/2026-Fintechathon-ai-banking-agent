"""Explicit administrator commands for full directory, on-demand evidence and pool."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from ...models import Base
from .wealth_provider import AKShareProvider
from . import wealth_fund_data as funds


async def run(args):
    if args.action == 'worker':
        if not args.live or os.getenv('WEALTH_MARKET_SYNC_ENABLED') != 'true': raise ValueError('EXPLICIT_ENABLED_LIVE_REQUIRED')
        from datetime import datetime, timezone
        from .wealth_data_cli import claim_job, finish_job
        while True:
            now = datetime.now(timezone.utc)
            schedule = [('directory', now.strftime('%G-W%V')), ('preload', now.date().isoformat())]
            for action, period in schedule:
                job = 'mixed-' + action + '-' + period
                # Bootstrap tables before claiming a first-run job.
                setup = create_async_engine(os.environ['DATABASE_URL'])
                async with setup.begin() as conn: await conn.run_sync(Base.metadata.create_all)
                await setup.dispose()
                if not await claim_job(os.environ['DATABASE_URL'], job): continue
                child = argparse.Namespace(**vars(args)); child.action = action
                child.force = False; child.rules = action == 'preload'; child.output = None
                try:
                    result = await run(child)
                    if action == 'preload':
                        child.action = 'compose'; await run(child)
                    await finish_job(os.environ['DATABASE_URL'], job, result)
                except Exception as error:
                    await finish_job(os.environ['DATABASE_URL'], job, {'ok':False,'error_type':type(error).__name__})
            if args.once: break
            await asyncio.sleep(max(60, args.interval))
        return
    url = os.environ['DATABASE_URL']
    engine = create_async_engine(url); sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    provider = AKShareProvider(timeout=args.timeout, retries=2)
    if args.action in {'directory', 'fetch', 'preload'} and not args.live: raise ValueError('EXPLICIT_LIVE_REQUIRED')
    if args.action == 'directory':
        raw = await asyncio.to_thread(provider.fetch, 'fund_name_em')
        async with sessions() as db: result = await funds.import_directory(db, raw)
    elif args.action == 'search':
        async with sessions() as db: result = await funds.search(db, args.query, args.fund_type, args.page)
    elif args.action == 'detail':
        async with sessions() as db: result = await funds.detail(db, args.code)
    elif args.action == 'compose':
        from .wealth_preload import compose
        from .wealth_research import import_dataset
        from .wealth_knowledge import seed_mixed
        async with sessions() as db:
            payload = await compose(db, Path(args.manifest).resolve().parent)
            result = await import_dataset(db, payload)
            if result['ok']:
                result['knowledge_documents'] = await seed_mixed(db, payload)
                from sqlalchemy import select
                from ...models.wealth_research import WealthMacroORM
                rows = (await db.scalars(select(WealthMacroORM))).all()
                latest = {}
                for row in rows:
                    actual = [o for o in row.observations if o.get('source') != 'synthetic']
                    if not actual: continue
                    available = max(o['available_at'] for o in actual)
                    if row.indicator not in latest or available > latest[row.indicator][0]: latest[row.indicator] = (available, actual)
                for indicator, (_, observations) in latest.items():
                    rid = result['dataset_key'] + ':' + indicator
                    if await db.get(WealthMacroORM, rid) is None:
                        db.add(WealthMacroORM(record_id=rid,dataset_key=result['dataset_key'],indicator=indicator,observations=observations))
                result['real_macro_series'] = len(latest)
                await db.commit()
            result.update(group_counts=payload['group_counts'], rejected=payload['rejected'])
    else:
        if args.action == 'preload':
            manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
            entries = manifest['funds']
        else: entries = [{'code': args.code}]
        results = []
        for entry in entries:
            code = entry['code']
            async with sessions() as db:
                identity = await db.get(funds.Directory, code)
                if identity is None:
                    results.append({'code': code, 'status': 'NOT_IN_DIRECTORY'}); continue
                kinds = ['money_yield' if '货币' in identity.fund_type else 'history']
                if args.rules: kinds += ['status', 'fees']
                if getattr(args, 'data_type', None): kinds = [args.data_type]
            for kind in kinds:
                async with sessions() as db: acquired = await funds.claim(db, code, kind, args.force)
                if acquired: await funds.refresh(sessions, code, kind, provider)
            async with sessions() as db:
                evidence = await funds.detail(db, code)
                results.append({'code': code, 'name': evidence['name'], 'fund_type': evidence['fund_type'],
                    'evidence': {k: {'status': v['status'], 'error_code': v['error_code'],
                        'count': len(v['data'].get('observations', [])), 'available_at': v['data'].get('available_at'),
                        'latest_date': v['data'].get('observations', [{}])[-1].get('date') if v['data'].get('observations') else None}
                        for k, v in evidence['evidence'].items()}})
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        failures = [{'code':r['code'],'data_type':k,'status':v['status'],'error_code':v['error_code']}
            for r in results for k,v in r.get('evidence',{}).items() if v['status'] != 'READY']
        valid_history = all(any(k in {'history','money_yield'} and v['status']=='READY' for k,v in r.get('evidence',{}).items()) for r in results)
        result = {'products':results,'ok':valid_history,'partial':bool(failures),'source_failures':failures,
            'acceptance_basis':'successful_history; transaction rules still unreviewed'}
    if args.output:
        output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.action != 'preload': print(json.dumps(result, ensure_ascii=False))
    await engine.dispose()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['directory', 'search', 'fetch', 'detail', 'preload', 'compose', 'worker'])
    parser.add_argument('--interval', type=int, default=3600); parser.add_argument('--once', action='store_true')
    parser.add_argument('--live', action='store_true'); parser.add_argument('--force', action='store_true')
    parser.add_argument('--rules', action='store_true'); parser.add_argument('--code')
    parser.add_argument('--data-type', choices=['history','money_yield','status','fees'])
    parser.add_argument('--query', default=''); parser.add_argument('--fund-type'); parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--manifest', default='config/wealth/preload.json'); parser.add_argument('--output')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__': main()
