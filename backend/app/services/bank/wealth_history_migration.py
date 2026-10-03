"""Explicit-account provenance splitting for pre-scope dialogue history; financial tables stay untouched."""
import argparse
import asyncio
import json
import os
from copy import deepcopy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from ...models import AccountORM
from ...models.wealth import WealthConversationORM, WealthTaskORM
from ...models.wealth_research import WealthContextORM
from ...domain.wealth_synthetic import fingerprint
from ...integrations.wealth_authorization import lock_profile


def account_refs(value):
    if isinstance(value, dict):
        return {item for key, item in value.items() if key == 'account_id' and isinstance(item, str)} | set().union(
            *(account_refs(item) for key,item in value.items() if key != 'account_id'))
    if isinstance(value, list): return set().union(*(account_refs(item) for item in value))
    return set()


async def split(db, *, apply=False):
    rows=list(await db.scalars(select(WealthConversationORM)))
    results=[]
    for source in rows:
        if not source.data.get('history_archive'): continue
        await lock_profile(db, source.user_id)
        owned=set(await db.scalars(select(AccountORM.account_id).where(AccountORM.user_id==source.user_id)))
        contexts=list(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==source.user_id)))
        scopes={row.account_id:row.context_id for row in contexts}
        origin=source.data.get('context_id','legacy-v1')
        partitions={}
        for section in ('history_archive','history'):
            for index,turn in enumerate(source.data.get(section,[])):
                refs=account_refs(turn)
                if not refs <= owned: raise ValueError('HISTORY_FOREIGN_ACCOUNT_REQUIRES_REVIEW')
                candidates={scopes.get(account,'legacy-v1') for account in refs}
                # No inference from dates, prose, adjacency or amounts.
                scope=next(iter(candidates)) if len(candidates)==1 else 'legacy-unassigned'
                if len(candidates)>1: raise ValueError('HISTORY_CROSS_SCOPE_TURN_REQUIRES_REVIEW')
                partitions.setdefault(scope,[]).append({**deepcopy(turn),'provenance':{
                    'source_session_id':source.session_id,'section':section,'index':index,
                    'content_hash':fingerprint(turn),'assignment':'explicit_account' if refs else 'unassigned_read_only'}})
        ids={scope:source.session_id if scope==origin else 'WCsplit'+fingerprint([source.session_id,scope])[:32]
             for scope in partitions}
        results.append({'source_session_id':source.session_id,'source_hash':fingerprint(source.data),
            'partitions':{scope:len(turns) for scope,turns in partitions.items()},'financial_fields_changed':False})
        if not apply: continue
        for scope,history in partitions.items():
            if scope==origin: continue
            target=await db.get(WealthConversationORM,ids[scope])
            if target: raise ValueError('HISTORY_TARGET_CONFLICT')
            db.add(WealthConversationORM(session_id=ids[scope],user_id=source.user_id,version=1,
                data={'context_id':scope,'history':history,'pending':{},'candidates':[],
                      'legacy_read_only':True,'scope_archived':True,'split_source':source.session_id}))
        source.version+=1
        source.data={**source.data,'history':partitions.get(origin,[]),'history_archive':[],
            'history_split':results[-1], 'pending':{},'active_command':None,'active_plan':None,'active_task':None}
        tasks=list(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.session_id==source.session_id)))
        for task in tasks:
            scope=scopes.get(task.account_id,'legacy-v1')
            if scope!=origin:
                if scope not in ids: raise ValueError('HISTORY_TASK_SCOPE_REQUIRES_REVIEW')
                task.session_id=ids[scope]
    if apply: await db.commit()
    else: await db.rollback()
    return {'mode':'apply' if apply else 'dry_run','rows':results,'changed_money_or_units':False}


async def main(apply):
    engine=create_async_engine(os.environ['DATABASE_URL'])
    try:
        async with async_sessionmaker(engine,expire_on_commit=False)() as db:
            print(json.dumps(await split(db,apply=apply),ensure_ascii=False,indent=2))
    finally: await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='先备份及副本验证；只按显式账户拆分旧混合历史，默认只读')
    parser.add_argument('--apply',action='store_true')
    asyncio.run(main(parser.parse_args().apply))
