"""蛋糕店订单的定价、幂等、权限与付款状态映射。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, PaymentRequestORM, UserORM
from backend.app.services.bank import payment_request_service
from backend.app.services.cake_shop import order_service
from backend.app.services.cake_shop.order_service import CakeShopError


@pytest_asyncio.fixture
async def cake_store(monkeypatch):
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
                    user_id="U008", name="甜时光蛋糕店", phone="13800000007",
                    id_card="demo", password_hash="hash", status="active",
                ),
                AccountORM(
                    account_id="A1", user_id="U1", account_type="savings",
                    card_number="1", balance=1000.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
                AccountORM(
                    account_id="A009", user_id="U008", account_type="savings",
                    card_number="8", balance=100.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
            ]
        )
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_server_price_and_idempotent_order_creation(cake_store) -> None:
    created = await order_service.create_order(
        customer_user_id="U1",
        product_id="STRAWBERRY_CREAM",
        quantity=2,
        idempotency_key="cake-order-key-001",
    )
    repeated = await order_service.create_order(
        customer_user_id="U1",
        product_id="STRAWBERRY_CREAM",
        quantity=2,
        idempotency_key="cake-order-key-001",
    )

    assert created["unit_price_fen"] == 9900
    assert created["total_fen"] == 19800
    assert created["payment_request_id"]
    assert created["payment_request"]["requester_name"] == "甜时光蛋糕店"
    assert created["payment_request"]["status"] == "PENDING"
    assert repeated["order_id"] == created["order_id"]
    assert repeated["payment_request_id"] == created["payment_request_id"]

    with pytest.raises(CakeShopError) as conflict:
        await order_service.create_order(
            customer_user_id="U1",
            product_id="CHOCOLATE_DELIGHT",
            quantity=1,
            idempotency_key="cake-order-key-001",
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_order_access_and_payment_status_reconciliation(cake_store) -> None:
    created = await order_service.create_order(
        customer_user_id="U1",
        product_id="CHOCOLATE_DELIGHT",
        quantity=1,
        idempotency_key="cake-order-key-002",
    )
    with pytest.raises(CakeShopError) as forbidden:
        await order_service.get_order(customer_user_id="U2", order_id=created["order_id"])
    assert forbidden.value.code == "ORDER_FORBIDDEN"

    async with cake_store() as session:
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
@pytest.mark.parametrize("payment_status", ["REJECTED", "CANCELLED", "EXPIRED"])
async def test_closed_payment_closes_order(cake_store, payment_status) -> None:
    created = await order_service.create_order(
        customer_user_id="U1",
        product_id="STRAWBERRY_CREAM",
        quantity=1,
        idempotency_key="cake-order-key-003",
    )
    async with cake_store() as session:
        payment = await session.get(PaymentRequestORM, created["payment_request_id"])
        payment.status = payment_status
        payment.state_version += 1
        await session.commit()

    reconciled = await order_service.get_order(
        customer_user_id="U1",
        order_id=created["order_id"],
    )
    assert reconciled["status"] == "PAYMENT_CLOSED"


@pytest.mark.asyncio
async def test_shop_isolation_and_merchant_permissions(cake_store, monkeypatch) -> None:
    from backend.app.services.flower_shop import order_service as flowers

    monkeypatch.setattr(flowers, "AsyncSessionLocal", cake_store)
    created = await order_service.create_order(
        customer_user_id="U1", product_id="STRAWBERRY_CREAM", quantity=1,
        idempotency_key="cake-isolation-001",
    )
    assert await flowers.list_orders(customer_user_id="U1") == []
    with pytest.raises(flowers.FlowerShopError) as missing:
        await flowers.get_order(customer_user_id="U1", order_id=created["order_id"])
    assert missing.value.code == "ORDER_NOT_FOUND"
    with pytest.raises(CakeShopError) as forbidden:
        await order_service.list_merchant_orders(merchant_user_id="U007")
    assert forbidden.value.code == "MERCHANT_FORBIDDEN"
    with pytest.raises(CakeShopError) as forbidden:
        await order_service.refund_order(
            merchant_user_id="U007", order_id=created["order_id"],
            state_version=created["state_version"],
        )
    assert forbidden.value.code == "MERCHANT_FORBIDDEN"
    with pytest.raises(CakeShopError) as unpaid:
        await order_service.refund_order(
            merchant_user_id="U008", order_id=created["order_id"],
            state_version=created["state_version"],
        )
    assert unpaid.value.code == "ORDER_NOT_REFUNDABLE"


@pytest.mark.asyncio
async def test_cake_http_contract(cake_store) -> None:
    import httpx
    from fastapi import FastAPI
    from backend.app.middleware.auth import get_current_user
    from backend.app.routers.cake_shop import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        products = await client.get("/api/v1/cake-shop/products")
        assert products.status_code == 200
        assert len(products.json()) == 3
        payload = {
            "product_id": "STRAWBERRY_CREAM", "quantity": 2,
            "idempotency_key": "cake-http-order-001",
        }
        response = await client.post("/api/v1/cake-shop/orders", json=payload)
        assert response.status_code == 201
        order = response.json()
        assert order["total_fen"] == 19800
        assert order["payment_request"]["requester_name"] == "甜时光蛋糕店"
        repeated = await client.post("/api/v1/cake-shop/orders", json=payload)
        assert repeated.json()["order_id"] == order["order_id"]
        listed = await client.get("/api/v1/cake-shop/orders")
        assert len(listed.json()) == 1
        assert (await client.get(f"/api/v1/cake-shop/orders/{order['order_id']}")).status_code == 200
        assert (await client.get("/api/v1/cake-shop/merchant/orders")).status_code == 403
        invalid = await client.post("/api/v1/cake-shop/orders", json={**payload, "quantity": 0})
        assert invalid.status_code == 422
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "U2"}
        assert (await client.get(f"/api/v1/cake-shop/orders/{order['order_id']}")).status_code == 403
