"""Fresh local joint bank, real A provider and public auth, no recorded Plan/NLP."""
import os
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
ROOT=Path(__file__).resolve().parents[2]
path=Path(os.environ['WEALTH_JOINT_DATABASE']).resolve()
assert path.is_relative_to(ROOT/'.deps/joint_validation') and path.name=='joint_bank.db'
EXISTING=path.exists()
assert not EXISTING or os.environ.get('WEALTH_JOINT_REUSE')=='1','Use a new path or explicitly reuse the same dedicated bank; never reset it'
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
from backend.app.database.connection import get_db
from backend.app.routers.wealth import get_wealth_bank
from backend.app.security.passwords import hash_password
BASE=datetime(2026,10,12,6,0,tzinfo=timezone.utc)
if os.environ.get('WEALTH_JOINT_CATALOG')=='real-paper':BASE=datetime(2026,10,13,6,0,tzinfo=timezone.utc)

async def test_clock(db,account_id=None):
    if account_id:
        from backend.app.models.wealth_research import WealthContextORM
        context=(await db.scalars(select(WealthContextORM).where(WealthContextORM.account_id==account_id))).one_or_none()
        if context and context.data.get('account_scope') == 'shared_bank':
            return datetime.now(timezone.utc)
        if context and context.data.get('joint_manual_clock') is True:
            return datetime.fromisoformat(context.data['business_at'])
    row=await db.get(WealthClockORM,'mock')
    return row.business_at.replace(tzinfo=timezone.utc) if row.business_at.tzinfo is None else row.business_at

@asynccontextmanager
async def lifespan(application):
    from backend.app.integrations import wealth_authorization as assessment_policy
    assessment_policy.ASSESSMENT_DAILY_LIMIT = 0 if os.getenv('WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT') == 'true' else 2
    assessment_policy.ASSESSMENT_ROLLING_YEAR_LIMIT = 0 if os.getenv('WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT') == 'true' else 8
    await application.state.wealth_portfolio_source.preload()
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    bank.business_now=test_clock
    if EXISTING:
        # Explicit restart keeps every existing account, assessment and order.
        yield
        await engine.dispose()
        return
    async with AsyncSessionLocal() as db:
        db.add_all([UserORM(user_id=uid,name='张三' if uid=='U1' else '李四',phone=f'1380000000{i}',
            id_card='joint-disposable',password_hash=hash_password('password123'),status='active') for i,uid in enumerate(('U1','U2'),1)])
        await db.flush()
        db.add_all([AccountORM(account_id=f'A{i}',user_id=f'U{i}',account_type='savings',card_number=f'joint-mock-{i}',
            balance_fen=2_000_000,daily_transfer_limit_fen=5_000_000) for i in (1,2)])
        from backend.app.domain.wealth import CALENDAR_VERSION
        db.add(WealthClockORM(clock_id='mock',business_at=BASE,calendar_version=CALENDAR_VERSION))
        await seed_catalog(db)
        catalog_mode=os.environ.get('WEALTH_JOINT_CATALOG','fixed-full')
        assert catalog_mode in {'legacy','fixed-synthetic','fixed-full','real-paper'}
        if catalog_mode!='legacy':
            # Existing immutable 60-product definition, no new product design,
            # model, ratings or prices; the original legacy catalogue remains.
            from backend.app.services.bank.wealth_research import initialize,select_context
            if catalog_mode=='real-paper':
                import json
                from backend.app.services.bank.wealth_research import import_dataset
                payload=json.loads((ROOT/'config/wealth/paper_products.json').read_text(encoding='utf8'))
                imported=await import_dataset(db,payload);assert imported['ok'],imported
                result=await initialize(db,'U1','normal',dataset_key=imported['dataset_key'])
                from backend.app.models.wealth_research import WealthContextORM
                context=await db.get(WealthContextORM,result['context_id'])
                context.data={**context.data,'label':'真实基金公开资料｜比赛模拟交易｜联合验收','business_at':BASE.isoformat()}
            elif catalog_mode=='fixed-full':
                from backend.app.services.bank.wealth_baseline_upgrade import fixed_payload
                from backend.app.services.bank.wealth_research import import_dataset
                imported=await import_dataset(db,await fixed_payload(db))
                assert imported['ok']
                result=await initialize(db,'U1','normal',dataset_key=imported['dataset_key'])
                from backend.app.models.wealth_research import WealthContextORM
                context=await db.get(WealthContextORM,result['context_id'])
                context.data={**context.data,'label':'既有固定78产品｜虚构银行与资金｜联合隔离诊断','business_at':BASE.isoformat()}
            else:result=await initialize(db,'U1','normal')
            await select_context(db,'U1',result['context_id'],1)
        await db.commit()
        # No prefilled assessment: the actual public questionnaire is part of browser acceptance.
    yield
    await engine.dispose()

app.router.lifespan_context=lifespan
class Advance(BaseModel):days:int=Field(ge=0,le=60)
class Diagnose(BaseModel):derive_current:bool=False
@app.post('/api/v1/joint-test/diagnose')
async def diagnosis(request:Diagnose,user:dict=Depends(get_current_user),db=Depends(get_db),bank_api=Depends(get_wealth_bank)):
    assert user['user_id'] in {'U1','ROUND2'}
    from tests.browser.wealth_joint_diagnosis import diagnose
    return await diagnose(db,user['user_id'],app.state.wealth_portfolio_source,bank_api,derive_current=request.derive_current)
@app.post('/api/v1/joint-test/advance')
async def advance(request:Advance,user:dict=Depends(get_current_user)):
    assert user['user_id'] in {'U1','ROUND2'}
    async with AsyncSessionLocal() as db:
        current=await test_clock(db);new=BASE+timedelta(days=request.days)
        assert new>=current,'Clock cannot move backwards'
        row=await db.get(WealthClockORM,'mock');row.business_at=new;await db.commit()
        orders=list(await db.scalars(select(WealthOrderORM).where(WealthOrderORM.user_id==user['user_id'])))
        for order in orders:
            if order.status not in {'CANCELLED','SETTLED','REJECTED'}:await bank.settle(db,order.operation_id)
    return {'source':'isolated-joint-bank','days':request.days,'orders_checked':len(orders)}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=port)
