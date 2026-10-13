"""Fresh local joint bank, real A provider and public auth, no recorded Plan/NLP."""
import os
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
ROOT=Path(__file__).resolve().parents[2]
path=Path(os.environ['WEALTH_JOINT_DATABASE']).resolve()
assert path.is_relative_to(ROOT/'.deps/joint_validation') and path.name=='joint_bank.db'
assert not path.exists(),'Use a new path; never reset an existing bank'
path.parent.mkdir(parents=True,exist_ok=True)
port=int(os.environ.get('WEALTH_JOINT_PORT','8013'));assert port in (8013,8014)
os.environ['DATABASE_URL']='sqlite+aiosqlite:///'+path.as_posix()
os.environ['BANK_API_BASE_URL']=f'http://127.0.0.1:{port}'
os.environ['INTERNAL_BANK_TOKEN']=uuid4().hex
os.environ['JWT_SECRET']=uuid4().hex
os.environ['LLM_MODE']='offline'
os.environ['SMS_PROVIDER']='mock'
os.environ['WEALTH_OPERATOR_USER_IDS']='U2'
from datetime import datetime,timezone,timedelta
from fastapi import Depends
from pydantic import BaseModel,Field
from sqlalchemy import select
from backend.app.main import app
from backend.app.database.connection import engine,AsyncSessionLocal
from backend.app.models import Base,UserORM,AccountORM
from backend.app.models.wealth import WealthClockORM,WealthOrderORM
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.wealth_catalog import seed_catalog
from backend.app.middleware.auth import get_current_user
from backend.app.security.passwords import hash_password
BASE=datetime(2026,10,12,6,0,tzinfo=timezone.utc)

async def test_clock(db,account_id=None):
    row=await db.get(WealthClockORM,'mock')
    return row.business_at.replace(tzinfo=timezone.utc) if row.business_at.tzinfo is None else row.business_at

@asynccontextmanager
async def lifespan(application):
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    bank.business_now=test_clock
    async with AsyncSessionLocal() as db:
        db.add_all([UserORM(user_id=uid,name='张三' if uid=='U1' else '李四',phone=f'1380000000{i}',
            id_card='joint-disposable',password_hash=hash_password('password123'),status='active') for i,uid in enumerate(('U1','U2'),1)])
        await db.flush()
        db.add_all([AccountORM(account_id=f'A{i}',user_id=f'U{i}',account_type='savings',card_number=f'joint-mock-{i}',
            balance_fen=2_000_000,daily_transfer_limit_fen=5_000_000) for i in (1,2)])
        from backend.app.domain.wealth import CALENDAR_VERSION
        db.add(WealthClockORM(clock_id='mock',business_at=BASE,calendar_version=CALENDAR_VERSION))
        await seed_catalog(db);await db.commit()
        # No prefilled assessment: the actual public questionnaire is part of browser acceptance.
    yield
    await engine.dispose()

app.router.lifespan_context=lifespan
class Advance(BaseModel):days:int=Field(ge=0,le=60)
@app.post('/api/v1/joint-test/advance')
async def advance(request:Advance,user:dict=Depends(get_current_user)):
    assert user['user_id']=='U1'
    async with AsyncSessionLocal() as db:
        current=await test_clock(db);new=BASE+timedelta(days=request.days)
        assert new>=current,'Clock cannot move backwards'
        row=await db.get(WealthClockORM,'mock');row.business_at=new;await db.commit()
        orders=list(await db.scalars(select(WealthOrderORM).where(WealthOrderORM.user_id=='U1')))
        for order in orders:
            if order.status not in {'CANCELLED','SETTLED','REJECTED'}:await bank.settle(db,order.operation_id)
    return {'source':'isolated-joint-bank','days':request.days,'orders_checked':len(orders)}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=port)
