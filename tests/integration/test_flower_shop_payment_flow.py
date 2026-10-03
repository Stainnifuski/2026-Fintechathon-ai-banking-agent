"""鲜花下单到银行原子转账、订单完成的集成链路。"""

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.integrations.bank_api import (
    BankApiError,
    BankOperationResult,
    ResolvedTransferParties,
    TransferValidation,
)
from backend.app.models import AccountORM, Base, TransactionORM, UserORM
from backend.app.services.bank import (
    BankError,
    merchant_refund_service,
    payment_request_service,
    query_operation,
    release_payment_request_payment,
    submit_transfer,
    validate_transfer_parties,
    resolve_transfer_parties,
)
from backend.app.services.bank import migration_service
from backend.app.services.flower_shop import order_service
from backend.app.services.orchestration import (
    accept_task,
    confirm_task,
    create_transfer_task,
)


class InProcessBankApi:
    def __init__(self, db) -> None:
        self.db = db

    async def validate_transfer(self, **kwargs) -> TransferValidation:
        try:
            result = await validate_transfer_parties(self.db, **kwargs)
        except BankError as error:
            raise BankApiError(error.message, error.code, error.status_code) from error
        return TransferValidation(**result.__dict__)

    async def resolve_transfer_parties(self, **kwargs) -> ResolvedTransferParties:
        try:
            result = await resolve_transfer_parties(self.db, **kwargs)
        except BankError as error:
            raise BankApiError(error.message, error.code, error.status_code) from error
        return ResolvedTransferParties(**result.__dict__)

    async def submit_transfer(self, command, context, **kwargs) -> BankOperationResult:
        try:
            result = await submit_transfer(self.db, command, context, **kwargs)
        except BankError as error:
            raise BankApiError(error.message, error.code, error.status_code) from error
        return BankOperationResult(**result.__dict__)

    async def query_operation(self, operation_id: str) -> BankOperationResult | None:
        result = await query_operation(self.db, operation_id)
        return BankOperationResult(**result.__dict__) if result else None

    async def release_payment_request(self, **kwargs) -> None:
        await release_payment_request_payment(self.db, **kwargs)


@pytest_asyncio.fixture
async def flower_payment_store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(order_service, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(payment_request_service, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(merchant_refund_service, "AsyncSessionLocal", sessions)
    async with sessions() as session:
        session.add_all(
            [
                UserORM(
                    user_id="U1", name="顾客", phone="13800000001",
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
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_flower_order_payment_completes_through_transfer_task(
    flower_payment_store,
) -> None:
    order = await order_service.create_order(
        customer_user_id="U1",
        product_id="ROSE_CLASSIC",
        quantity=1,
        idempotency_key="flower-integration-001",
    )
    payments = await payment_request_service.list_payment_requests(
        user_id="U1",
        role="incoming",
    )
    payment = next(
        item for item in payments
        if item["payment_request_id"] == order["payment_request_id"]
    )
    assert payment["requester_user_id"] == "U007"
    assert payment["payer_user_id"] == "U1"
    assert payment["receive_account_id"] == "A008"

    prepared = await payment_request_service.prepare_payment_request_payment(
        payment_request_id=payment["payment_request_id"],
        payer_user_id="U1",
        payer_account_id="A1",
        state_version=payment["state_version"],
    )
    bank = InProcessBankApi(flower_payment_store)
    task = await create_transfer_task(
        flower_payment_store,
        bank,
        user_id="U1",
        request_id=f"PRTASK-{prepared['operation_id']}",
        operation_id=prepared["operation_id"],
        from_account=prepared["payer_account_id"],
        to_account=prepared["receive_account_id"],
        amount=prepared["amount"],
        note=prepared["note"],
    )
    ready = await confirm_task(
        flower_payment_store,
        task_id=task["task_id"],
        user_id="U1",
        request_id="flower-confirm-001",
        state_version=task["state_version"],
        plan_version=task["plan_version"],
    )
    finished = await accept_task(
        flower_payment_store,
        bank,
        task_id=ready["task_id"],
        user_id="U1",
        request_id="flower-accept-001",
        state_version=ready["state_version"],
    )

    assert finished["phase"] == "FINISHED"
    assert finished["outcome"] == "SUCCEEDED"
    await flower_payment_store.refresh(await flower_payment_store.get(AccountORM, "A1"))
    await flower_payment_store.refresh(await flower_payment_store.get(AccountORM, "A008"))
    assert (await flower_payment_store.get(AccountORM, "A1")).balance_fen == 90100
    assert (await flower_payment_store.get(AccountORM, "A008")).balance_fen == 19900
    transactions = (
        await flower_payment_store.execute(
            select(TransactionORM).order_by(TransactionORM.type)
        )
    ).scalars().all()
    assert {(item.type, item.counterparty, item.category) for item in transactions} == {
        ("purchase", "花语鲜花店", "shopping"),
        ("merchant_sale", "顾客", "shopping"),
    }
    for item in transactions:
        if item.type == "purchase":
            item.type, item.counterparty, item.category = "transfer_out", "A008", "transfer"
        else:
            item.type, item.counterparty, item.category = "transfer_in", "A1", "transfer"
    await flower_payment_store.commit()
    await migration_service.upgrade_flower_purchase_transactions(
        async_sessionmaker(flower_payment_store.bind, expire_on_commit=False)
    )
    flower_payment_store.expire_all()
    migrated = (
        await flower_payment_store.execute(
            select(TransactionORM).order_by(TransactionORM.type)
        )
    ).scalars().all()
    assert {(item.type, item.counterparty, item.category) for item in migrated} == {
        ("purchase", "花语鲜花店", "shopping"),
        ("merchant_sale", "顾客", "shopping"),
    }

    reconciled = await order_service.get_order(
        customer_user_id="U1",
        order_id=order["order_id"],
    )
    assert reconciled["status"] == "COMPLETED"

    merchant_orders = await order_service.list_merchant_orders(
        merchant_user_id="U007",
    )
    assert merchant_orders[0]["payment_request"]["payer_name"] == "顾客"
    refunded = await order_service.refund_order(
        merchant_user_id="U007",
        order_id=order["order_id"],
        state_version=reconciled["state_version"],
    )
    assert refunded["status"] == "REFUNDED"
    await flower_payment_store.refresh(await flower_payment_store.get(AccountORM, "A1"))
    await flower_payment_store.refresh(await flower_payment_store.get(AccountORM, "A008"))
    assert (await flower_payment_store.get(AccountORM, "A1")).balance_fen == 100000
    assert (await flower_payment_store.get(AccountORM, "A008")).balance_fen == 10000
    refund_transactions = (
        await flower_payment_store.execute(
            select(TransactionORM).where(
                TransactionORM.type.in_(("refund", "refund_out"))
            )
        )
    ).scalars().all()
    assert {(item.type, item.counterparty) for item in refund_transactions} == {
        ("refund", "花语鲜花店"),
        ("refund_out", "顾客"),
    }

    repeated = await order_service.refund_order(
        merchant_user_id="U007",
        order_id=order["order_id"],
        state_version=refunded["state_version"],
    )
    assert repeated["status"] == "REFUNDED"
