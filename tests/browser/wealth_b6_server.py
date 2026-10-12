"""Loopback-only, disposable B6 browser bank. Never imported by production.

Run with B6_BROWSER_DATABASE pointing at a NEW .tmp child browser_bank.db.
It keeps the public JWT/confirmation/SMS APIs and the real internal bank boundary.
The only replaced components are the explicitly recorded A provider and test clock.
"""
from pathlib import Path
import os
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
path = Path(os.environ['B6_BROWSER_DATABASE']).resolve()
assert path.is_relative_to((ROOT/'.tmp').resolve()) and path.name=='browser_bank.db'
assert not path.exists(), 'Browser bank must be a new disposable file; no reset/delete is performed'
path.parent.mkdir(parents=True, exist_ok=True)
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///'+path.as_posix()
os.environ['BANK_API_BASE_URL'] = 'http://127.0.0.1:8011'
os.environ['INTERNAL_BANK_TOKEN'] = uuid4().hex
os.environ['LLM_MODE'] = 'offline'
os.environ['SMS_PROVIDER'] = 'mock'

from contextlib import asynccontextmanager
from datetime import timedelta, timezone
import httpx
from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from backend.app.main import app
from backend.app.database.connection import engine, AsyncSessionLocal
from backend.app.models import Base, UserORM, AccountORM
from backend.app.models.wealth import WealthClockORM, WealthOrderORM
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.wealth_catalog import seed_catalog
from backend.app.integrations.wealth_bank_api import HttpWealthBankApi
from backend.app.routers.wealth import get_wealth_bank
from backend.app.middleware.auth import get_current_user
from backend.app.security.passwords import hash_password
from tests.integration.test_wealth_bank import NOW, assess
from tests.integration.test_wealth_b6_manual_chain import SharedScopeSource, register_recorded_handlers


class BrowserBank(HttpWealthBankApi):
    async def _request(self, method, route, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated-bank') as client:
            return await client.request(method,route,headers=self._headers,**kwargs)


async def test_clock(db, account_id=None):
    row = await db.get(WealthClockORM,'mock')
    return row.business_at.replace(tzinfo=timezone.utc) if row.business_at.tzinfo is None else row.business_at


@asynccontextmanager
async def lifespan(application):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bank.business_now = test_clock
    async with AsyncSessionLocal() as db:
        db.add_all([UserORM(user_id=uid,name='B6 隔离验收（固定方案）' if uid=='U1' else '隔离收款对象',
                    phone=f'1380000000{i}',id_card='disposable-fixture',password_hash=hash_password('password123'),status='active')
                    for i,uid in enumerate(('U1','U2'),1)])
        await db.flush()
        db.add_all([AccountORM(account_id=f'A{i}',user_id=f'U{i}',account_type='savings',card_number=f'b6-mock-{i}',
                               balance_fen=2_000_000,daily_transfer_limit_fen=5_000_000) for i in (1,2)])
        from backend.app.domain.wealth import CALENDAR_VERSION
        db.add(WealthClockORM(clock_id='mock',business_at=NOW,calendar_version=CALENDAR_VERSION))
        await seed_catalog(db)
        await db.commit()
        await assess(db)  # Explicit test-profile fixture, not a real customer's answers.
    local=BrowserBank()
    application.dependency_overrides[get_wealth_bank]=lambda:local
    await register_recorded_handlers(SharedScopeSource(None),local,application)
    yield
    await engine.dispose()


app.router.lifespan_context=lifespan  # No production seeding or background workers.


class Advance(BaseModel):
    days:int=Field(ge=0,le=60)


@app.post('/api/v1/b6-test/advance')
async def advance(request:Advance,user:dict=Depends(get_current_user)):
    assert user['user_id']=='U1'
    async with AsyncSessionLocal() as db:
        clock=await db.get(WealthClockORM,'mock')
        assert NOW+timedelta(days=request.days)>=await test_clock(db), 'Clock cannot go backwards'
        clock.business_at=NOW+timedelta(days=request.days)
        await db.commit()
        orders=list(await db.scalars(select(WealthOrderORM).where(WealthOrderORM.user_id=='U1')))
        for order in orders:
            if order.status not in {'CANCELLED','SETTLED','REJECTED'}:
                await bank.settle(db,order.operation_id)
    return {'environment':'disposable-b6-bank','days':request.days,'orders_checked':len(orders)}


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8011)
