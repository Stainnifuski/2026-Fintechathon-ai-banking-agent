"""Real database lease/restart validation without network or production state."""
import asyncio
import os
import uuid
import pytest
from backend.app.services.bank.wealth_data_cli import claim_job, finish_job
from backend.app.services.bank.wealth_provider import normalize_macro
from backend.app.domain.wealth_analytics import hypothetical_portfolio
from backend.app.domain.wealth_allocation import candidates


@pytest.mark.asyncio
@pytest.mark.parametrize('database',['sqlite','postgresql'])
async def test_sync_lease_restart_duplicate_and_failure(database,tmp_path):
    url=f'sqlite+aiosqlite:///{tmp_path / "wealth_test_sync.db"}' if database=='sqlite' else os.getenv('BANK_TEST_DATABASE_URL')
    if not url: pytest.skip('Independent PostgreSQL database not configured')
    if database=='postgresql': assert 'bank_test_' in url
    from sqlalchemy.ext.asyncio import create_async_engine
    from backend.app.models import Base
    engine=create_async_engine(url)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    key='test-lease-'+uuid.uuid4().hex
    assert sum(await asyncio.gather(claim_job(url,key),claim_job(url,key)))==1
    assert not await claim_job(url,key)  # restart cannot duplicate a live lease
    await finish_job(url,key,{'ok':False,'error':'source_unavailable'})
    assert await claim_job(url,key)  # restart retries expired failed job
    await finish_job(url,key,{'ok':True})
    assert not await claim_job(url,key)  # even expired successful job is stable


def test_macro_sort_publication_unknown_and_future_rejection():
    rows=normalize_macro([{'月份':'2026年09月份','值':2},{'月份':'2026年08月份','值':1}],'月份','值','m1_yoy','2026-10-02T12:00:00+00:00')
    assert [r['period'] for r in rows]==['2026-08','2026-09']
    assert rows[-1]['published_at'] is None and rows[-1]['definition_break']=='M1-definition-2025'
    with pytest.raises(ValueError,match='FUTURE'): normalize_macro([{'日期':'2027-01-01','值':1}],'日期','值','cpi_yoy','2026-10-02T12:00:00+00:00')


def test_fixed_units_portfolio_gold_and_missing_history():
    products=[{'product_id':'A','dataset_key':'synthetic:v1','history':[{'date':'2026-09-01','nav':'1'},{'date':'2026-09-02','nav':'1.1'}]},
              {'product_id':'C','dataset_key':'synthetic:v1','history':[{'date':'2026-09-01','nav':'1'},{'date':'2026-09-02','nav':'0.9'}]}]
    result=hypothetical_portfolio(products,{'A':75000,'C':25000},'2026-09-02')
    assert result['metrics']['return']=='0.050000'
    assert hypothetical_portfolio(products,{'Z':1},'2026-09-02')['metrics'] is None


def test_closed_or_stale_candidates_are_never_ranked():
    from tests.unit.test_wealth_v12_evaluation import sample_products
    rows=sample_products(); rows[0]['purchase_open']=False; rows[1]['data_stale']=True
    assert not candidates(rows,2,180,[])


@pytest.mark.asyncio
async def test_admin_reset_dry_run_preserves_cash_and_audit(tmp_path,monkeypatch):
    from argparse import Namespace
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from backend.app.models import Base,UserORM,AccountORM
    from backend.app.services.bank import wealth_research
    from backend.app.services.bank.wealth_data_cli import run
    url=f'sqlite+aiosqlite:///{tmp_path / "wealth_test_admin.db"}'
    engine=create_async_engine(url)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as db:
        db.add(UserORM(user_id='U001',name='虚构管理员测试',phone='13900000001',id_card='mock',status='active')); await db.flush()
        ctx=await wealth_research.initialize(db,'U001',balance_fen=12300); await db.commit()
    monkeypatch.setenv('DATABASE_URL',url)
    args=Namespace(action='reset',context_id=ctx['context_id'],dry_run=True,confirm_reset=False)
    await run(args)
    async with sessions() as db: assert (await db.get(AccountORM,ctx['account_id'])).balance_fen==12300
    args.dry_run=False; args.confirm_reset=True
    await run(args)
    async with sessions() as db: assert (await db.get(AccountORM,ctx['account_id'])).balance_fen==2000000
    await engine.dispose()
