"""Explicit local installation; no startup mutation, collection or funding."""
import argparse,asyncio,os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from ...domain.wealth_bank_portfolio_bundle import read
from ...models.wealth_research import WealthContextORM,WealthContextSelectionORM
from .wealth_research import import_dataset
from .audit_service import record_bank_event

async def install(db,root,user_id,context_id=None):
    value,sha=read(root)
    # File-level scope policy is separate from unchanged transaction rules.
    payload={k:v for k,v in value.items() if k!='policy_version'}
    result=await import_dataset(db,payload)
    if not result['ok']:raise ValueError('BANK_SUPPLEMENT_INVALID')
    if context_id is None:
        selected=await db.get(WealthContextSelectionORM,user_id)
        context_id=selected.context_id if selected else None
    context=await db.get(WealthContextORM,context_id) if context_id else None
    if not context or context.user_id!=user_id or context.data.get('disposable') is not True or context.data.get('run_state','ACTIVE')!='ACTIVE':raise ValueError('OWNED_ACTIVE_SIMULATION_CONTEXT_REQUIRED')
    if not context.dataset_key.startswith('real-public-paper-v1:'):raise ValueError('REAL_FUND_CONTEXT_REQUIRED')
    existing=context.data.get('additional_dataset_keys',[])
    if result['dataset_key'] not in existing:
        context.data={**context.data,'additional_dataset_keys':[*existing,result['dataset_key']],
            'bank_supplement_sha256':sha,'bank_supplement_policy':value['policy_version']}
        context.version+=1
        record_bank_event(db,event_key=context.context_id+':'+sha,subject_user_id=user_id,
            action='link_existing_bank_supplement',result='success',facts={'context_id':context.context_id,
                'dataset_key':result['dataset_key'],'source_sha256':sha,'product_count':len(value['products'])},
            decision_chain=['owned_active_simulation_context','immutable_original_risks_rules_prices','no_funding_no_mfa_grant'])
    await db.commit()
    return {'context_id':context.context_id,'dataset_key':result['dataset_key'],'products':len(value['products']),'cash_changed':False}

async def main(args):
    url=os.environ[args.database_env]
    engine=create_async_engine(url)
    async with async_sessionmaker(engine,expire_on_commit=False)() as db:
        result=await install(db,Path(__file__).resolve().parents[4],args.user_id,args.context_id)
        print(__import__('json').dumps(result,ensure_ascii=False))
    await engine.dispose()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database-env',default='DATABASE_URL');p.add_argument('--user-id',required=True);p.add_argument('--context-id')
    asyncio.run(main(p.parse_args()))
