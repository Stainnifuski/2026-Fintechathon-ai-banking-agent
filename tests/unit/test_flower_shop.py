"""花店订单的定价、幂等、权限与付款状态映射。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, PaymentRequestORM, UserORM
from backend.app.services.bank import payment_request_service
from backend.app.services.flower_shop import order_service
from backend.app.services.flower_shop.order_service import FlowerShopError


@pytest_asyncio.fixture
async def flower_store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(order_service, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(payment_request_service, "AsyncSessionLocal", sessions)
    async with sessions() as session:
        session.add_all(
            [
                UserORM(
                    user_id="U1", name="顾客", phone="13800000001",
                    id_card="demo", password_hash="hash", status="active",
                ),
                UserORM(
                    user_id="U2", name="其他顾客", phone="13800000002",
                    id_card="demo", password_hash="hash", status="active",
                ),
                UserORM(
                    user_id="U007", name="花语鲜花店", phone="13800000007",
                    id_card="demo", password_hash="hash", status="active",
                ),
                AccountORM(
                    account_id="A1", user_id="U1", account_type="savings",
                    card_number="1", balance=1000.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
                AccountORM(
                    account_id="A008", user_id="U007", account_type="savings",
                    card_number="8", balance=100.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
            ]
        )
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_server_price_and_idempotent_order_creation(flower_store) -> None:
    created = await order_service.create_order(
        customer_user_id="U1",
        product_id="ROSE_CLASSIC",
        quantity=2,
        idempotency_key="flower-order-key-001",
    )
    repeated = await order_service.create_order(
        customer_user_id="U1",
        product_id="ROSE_CLASSIC",
        quantity=2,
        idempotency_key="flower-order-key-001",
    )

    assert created["unit_price_fen"] == 9900
    assert created["total_fen"] == 19800
    assert created["payment_request_id"]
    assert created["payment_request"]["requester_name"] == "花语鲜花店"
    assert created["payment_request"]["status"] == "PENDING"
    assert repeated["order_id"] == created["order_id"]
    assert repeated["payment_request_id"] == created["payment_request_id"]

    with pytest.raises(FlowerShopError) as conflict:
        await order_service.create_order(
            customer_user_id="U1",
            product_id="SUNSHINE",
            quantity=1,
            idempotency_key="flower-order-key-001",
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_order_access_and_payment_status_reconciliation(flower_store) -> None:
    created = await order_service.create_order(
        customer_user_id="U1",
        product_id="SUNSHINE",
        quantity=1,
        idempotency_key="flower-order-key-002",
    )
    with pytest.raises(FlowerShopError) as forbidden:
        await order_service.get_order(customer_user_id="U2", order_id=created["order_id"])
    assert forbidden.value.code == "ORDER_FORBIDDEN"

    async with flower_store() as session:
        payment = await session.get(PaymentRequestORM, created["payment_request_id"])
        payment.status = "PAID"
        payment.state_version += 1
        await session.commit()

    reconciled = await order_service.get_order(
        customer_user_id="U1",
        order_id=created["order_id"],
    )
    assert reconciled["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_rejected_payment_closes_order(flower_store) -> None:
    created = await order_service.create_order(
        customer_user_id="U1",
        product_id="ROSE_CLASSIC",
        quantity=1,
        idempotency_key="flower-order-key-003",
    )
    async with flower_store() as session:
        payment = await session.get(PaymentRequestORM, created["payment_request_id"])
        payment.status = "REJECTED"
        payment.state_version += 1
        await session.commit()

    reconciled = await order_service.get_order(
        customer_user_id="U1",
        order_id=created["order_id"],
    )
    assert reconciled["status"] == "PAYMENT_CLOSED"
