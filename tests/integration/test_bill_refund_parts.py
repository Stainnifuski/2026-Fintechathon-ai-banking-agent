"""Actual file-backed bank refunds: cumulative limits, authorization, replay, concurrency."""
import asyncio
from datetime import datetime,timezone,timedelta
import pytest
import pytest_asyncio
from sqlalchemy import select,func,event
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from backend.app.models import Base,UserORM,AccountORM,TransactionORM
from backend.app.models.payment_request import PaymentRequestORM
from backend.app.models.payment_request_payment import PaymentRequestPaymentORM
from backend.app.models.bank_operation import BankOperationORM
from backend.app.models.bill_features import MerchantRefundPartORM as Part
from backend.app.models.bill_foundation import BillTransactionMetaORM as Meta
from backend.app.services.bank import merchant_refund_service as bank,payment_request_service
from backend.app.services.bank.bill_foundation import sync_users,metadata_views
from backend.app.services.bank.transfer_service import BankError
from backend.app.services.bank.bill_service import analyze,TZ
from backend.app.schemas.bills import BillQuery


async def seed(session):
    for uid,name,phone in [('U1','顾客','13800000001'),('U007','花店','13800000007'),('U2','其他人','13800000002')]:
        session.add(UserORM(user_id=uid,name=name,phone=phone,id_card='demo',password_hash='hash',status='active'))
    await session.flush()
    session.add_all([AccountORM(account_id='A1',user_id='U1',account_type='savings',card_number='one',balance_fen=90000),AccountORM(account_id='A008',user_id='U007',account_type='savings',card_number='merchant',balance_fen=20000)])
    await session.flush()
    now=datetime.now(timezone.utc)
    session.add(PaymentRequestORM(payment_request_id='paid',requester_user_id='U007',payer_user_id='U1',receive_account_id='A008',amount_fen=10000,currency='CNY',status='PAID',state_version=1,idempotency_key='paid-key',expires_at=now))
    session.add(BankOperationORM(operation_id='purchase-op',status='SUCCEEDED',param_digest='x',from_account='A1',to_account='A008',amount_fen=10000,currency='CNY',bank_tx_id='original'))
    session.add(TransactionORM(tx_id='original',operation_id='purchase-op',account_id='A1',type='purchase',amount_fen=10000,counterparty='花店',category='shopping',timestamp=now-timedelta(days=40),status='completed',channel='app'))
    await session.flush();session.add(PaymentRequestPaymentORM(payment_request_id='paid',operation_id='purchase-op',payer_account_id='A1'))
    await session.flush();await sync_users(session,['U1','U007']);await session.commit()


@pytest_asyncio.fixture
async def store(tmp_path,monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'refund.db'))
    @event.listens_for(engine.sync_engine,'connect')
    def fk(connection,record):connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(bank,'AsyncSessionLocal',sessions);monkeypatch.setattr(payment_request_service,'AsyncSessionLocal',sessions)
    async with sessions() as session:await seed(session)
    yield sessions
    await engine.dispose()


async def refund(amount,operation,**kwargs):
    return await bank.refund_merchant_payment(payment_request_id='paid',merchant_user_id='U007',operation_id=operation,amount_fen=amount,**kwargs)


@pytest.mark.asyncio
async def test_multiple_partial_refunds_original_linking_and_replay(store):
    first=await refund(3000,'part1',expected_refunded_fen=0)
    await refund(3000,'part1') # exactly-once replay
    second=await refund(2000,'part2',expected_refunded_fen=3000)
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(Part))==2
        meta=await metadata_views(db,'U1',['original',first['customer_tx_id'],second['customer_tx_id']])
        assert meta['original']['refunded_fen']==5000 and meta['original']['refundable_fen']==5000
        assert len(meta['original']['refund_tx_ids'])==2
        assert meta[first['customer_tx_id']]['original_tx_id']=='original'
        assert (await db.get(AccountORM,'A1')).balance_fen==95000
    final=await refund(None,'last',expected_refunded_fen=5000)
    assert final['amount_fen']==5000 and final['remaining_fen']==0
    async with store() as db:
        assert (await db.get(AccountORM,'A1')).balance_fen==100000
        assert (await db.get(AccountORM,'A008')).balance_fen==10000
        assert (await metadata_views(db,'U1',['original']))['original']['refundable_fen']==0
    with pytest.raises(BankError) as capped:await refund(1,'over')
    assert capped.value.code=='PAYMENT_ALREADY_REFUNDED'


@pytest.mark.asyncio
async def test_refund_cap_parameter_binding_and_foreign_replay(store):
    await refund(3000,'first')
    with pytest.raises(BankError) as mismatch:await refund(2000,'first')
    assert mismatch.value.code=='IDEMPOTENCY_CONFLICT'
    with pytest.raises(BankError) as cap:await refund(8000,'oversize')
    assert cap.value.code=='REFUND_EXCEEDS_REMAINING'
    with pytest.raises(BankError) as foreign:
        await bank.refund_merchant_payment(payment_request_id='paid',merchant_user_id='U2',operation_id='first',amount_fen=3000)
    assert foreign.value.code=='MERCHANT_REFUND_FORBIDDEN'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(Part))==1
        assert (await db.get(AccountORM,'A1')).balance_fen==93000


@pytest.mark.asyncio
async def test_concurrent_partial_refunds_serialize_original_remaining_amount(store):
    async def attempt(op):
        try:return await refund(7000,op,expected_refunded_fen=0)
        except BankError as error:return error.code
    results=await asyncio.gather(attempt('concurrent-a'),attempt('concurrent-b'))
    assert sum(isinstance(r,dict) for r in results)==1
    assert 'REFUND_CHANGED' in results
    async with store() as db:
        assert await db.scalar(select(func.sum(Part.amount_fen)))==7000
        assert (await db.get(AccountORM,'A1')).balance_fen==97000
        assert (await db.get(AccountORM,'A008')).balance_fen==13000


@pytest.mark.asyncio
async def test_partial_order_can_continue_refunding_and_customer_sees_remaining(store,monkeypatch):
    from backend.app.models.flower_order import FlowerOrderORM
    from backend.app.services.flower_shop import order_service as shop
    monkeypatch.setattr(shop,'AsyncSessionLocal',store)
    async with store() as db:
        db.add(FlowerOrderORM(order_id='order',customer_user_id='U1',product_id='ROSE_CLASSIC',product_name='鲜花',product_description='花',quantity=1,unit_price_fen=10000,total_fen=10000,currency='CNY',payment_request_id='paid',status='COMPLETED',state_version=1,request_digest='x',idempotency_key='order'))
        await db.commit()
    first=await shop.refund_order(merchant_user_id='U007',order_id='order',state_version=1,amount_fen=3000,operation_id='shop1')
    assert first['status']=='PARTIALLY_REFUNDED' and first['refunded_fen']==3000 and first['remaining_refundable_fen']==7000
    observed=await shop.get_order(customer_user_id='U1',order_id='order')
    assert observed['status']=='PARTIALLY_REFUNDED'
    second=await shop.refund_order(merchant_user_id='U007',order_id='order',state_version=first['state_version'],amount_fen=7000,operation_id='shop2')
    assert second['status']=='REFUNDED' and second['refunded_fen']==10000
