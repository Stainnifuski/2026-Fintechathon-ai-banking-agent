"""Opt-in PostgreSQL contention acceptance, always isolated in a disposable schema.
BILL_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration/test_bill_postgres.py
"""
import asyncio,os
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text,select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from backend.app.models import Base
from backend.app.models.bill_features import MerchantRefundPartORM as Part
from backend.app.schemas.bill_foundation import AnnotationUpdate
from backend.app.services.bank import bill_foundation as foundation,merchant_refund_service as bank
from backend.app.services.bank.transfer_service import BankError
from tests.integration.test_bill_refund_parts import seed

@pytest_asyncio.fixture
async def pg_store(monkeypatch):
    url=os.getenv('BILL_TEST_DATABASE_URL')
    if not url:pytest.skip('BILL_TEST_DATABASE_URL not configured')
    if not url.startswith('postgresql+asyncpg://'):pytest.fail('BILL_TEST_DATABASE_URL must use postgresql+asyncpg')
    schema='bill_test_'+uuid4().hex
    admin=create_async_engine(url)
    async with admin.begin() as connection:await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_async_engine(url,connect_args={'server_settings':{'search_path':schema}})
    try:
        async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        async with sessions() as session:await seed(session)
        monkeypatch.setattr(bank,'AsyncSessionLocal',sessions)
        yield sessions
    finally:
        await engine.dispose()
        async with admin.begin() as connection:await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()

@pytest.mark.asyncio
async def test_postgres_optimistic_edit_and_refund_locks_across_actual_sessions(pg_store):
    async def tag(name):
        async with pg_store() as session:
            try:
                await foundation.annotate(session,'U1','original',AnnotationUpdate(tags=[name],expected_version=0));await session.commit();return 'saved'
            except BankError as error:await session.rollback();return error.code
    assert set(await asyncio.gather(tag('first'),tag('second')))=={'saved','STALE_ANNOTATION'}
    async def refund(op):
        try:
            await bank.refund_merchant_payment(payment_request_id='paid',merchant_user_id='U007',operation_id=op,amount_fen=7000,expected_refunded_fen=0);return 'saved'
        except BankError as error:return error.code
    assert set(await asyncio.gather(refund('pg-a'),refund('pg-b')))=={'saved','REFUND_CHANGED'}
    async with pg_store() as session:assert await session.scalar(select(func.sum(Part.amount_fen)))==7000
