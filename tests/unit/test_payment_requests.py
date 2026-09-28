"""收款申请的银行边界、权限与幂等行为。"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, TransactionORM, UserORM
from backend.app.services.orchestration.task_service import parameter_digest
from backend.app.services.bank import payment_request_service
from backend.app.services.bank.transfer_service import (
    BankError,
    BankTransferCommand,
    TrustedExecutionContext,
    submit_transfer,
)


@pytest_asyncio.fixture
async def payment_request_store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(payment_request_service, "AsyncSessionLocal", sessions)
    async with sessions() as session:
        session.add_all(
            [
                UserORM(
                    user_id="U1", name="张三", phone="13800000001", id_card="demo",
                    password_hash="hash", status="active",
                ),
                UserORM(
                    user_id="U2", name="李四", phone="13900000002", id_card="demo",
                    password_hash="hash", status="active",
                ),
                UserORM(
                    user_id="U3", name="王五", phone="13700000003", id_card="demo",
                    password_hash="hash", status="active",
                ),
                AccountORM(
                    account_id="A1", user_id="U1", account_type="savings",
                    card_number="1", balance=100.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
                AccountORM(
                    account_id="A2", user_id="U2", account_type="savings",
                    card_number="2", balance=100.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
            ]
        )
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_list_and_accept_payment_request(payment_request_store) -> None:
    created = await payment_request_service.create_payment_request(
        requester_user_id="U1",
        payer_user_id="U2",
        receive_account_id="A1",
        amount=12.34,
        note="午餐",
        idempotency_key="request-key-001",
    )
    assert created["amount_fen"] == 1234
    assert created["status"] == "PENDING"

    incoming = await payment_request_service.list_payment_requests(
        user_id="U2", role="incoming"
    )
    assert [item["payment_request_id"] for item in incoming] == [
        created["payment_request_id"]
    ]

    prepared = await payment_request_service.prepare_payment_request_payment(
        payment_request_id=created["payment_request_id"],
        payer_user_id="U2",
        payer_account_id="A2",
        state_version=created["state_version"],
    )
    requests = await payment_request_service.list_payment_requests(
        user_id="U2", role="incoming"
    )
    assert requests[0]["status"] == "ACCEPTED"

    async with payment_request_store() as session:
        result = await submit_transfer(
            session,
            BankTransferCommand(
                operation_id=prepared["operation_id"],
                from_account="A2",
                to_account="A1",
                amount_fen=1234,
                currency="CNY",
                param_digest=parameter_digest("A2", "A1", 1234, "CNY", "午餐"),
                note="午餐",
            ),
            TrustedExecutionContext(
                subject_user_id="U2", authorization_id="AUTH1", control_epoch=1
            ),
        )
    assert result.status == "SUCCEEDED"

    async with payment_request_store() as session:
        payer_account = await session.get(AccountORM, "A2")
        receive_account = await session.get(AccountORM, "A1")
        transactions = (
            await session.execute(
                select(TransactionORM).order_by(TransactionORM.type)
            )
        ).scalars().all()
    assert payer_account.balance_fen == 8766
    assert payer_account.daily_transfer_used_fen == 1234
    assert receive_account.balance_fen == 11234
    assert {(item.account_id, item.type, item.amount_fen) for item in transactions} == {
        ("A1", "transfer_in", 1234),
        ("A2", "transfer_out", 1234),
    }

    with pytest.raises(BankError) as stale_action:
        await payment_request_service.respond_to_payment_request(
            payment_request_id=created["payment_request_id"],
            payer_user_id="U2",
            action="reject",
            state_version=2,
        )
    assert stale_action.value.code == "STALE_STATE_VERSION"


@pytest.mark.asyncio
async def test_payment_request_enforces_ownership_and_idempotency(payment_request_store) -> None:
    with pytest.raises(BankError) as wrong_account:
        await payment_request_service.create_payment_request(
            requester_user_id="U1", payer_user_id="U2", receive_account_id="A2",
            amount=10, note=None, idempotency_key="request-key-002",
        )
    assert wrong_account.value.code == "RECEIVER_FORBIDDEN"

    created = await payment_request_service.create_payment_request(
        requester_user_id="U1", payer_user_id="U2", receive_account_id="A1",
        amount=10, note=None, idempotency_key="request-key-003",
    )
    duplicate = await payment_request_service.create_payment_request(
        requester_user_id="U1", payer_user_id="U2", receive_account_id="A1",
        amount=10, note=None, idempotency_key="request-key-003",
    )
    assert duplicate["payment_request_id"] == created["payment_request_id"]

    with pytest.raises(BankError) as intruder:
        await payment_request_service.respond_to_payment_request(
            payment_request_id=created["payment_request_id"], payer_user_id="U3",
            action="reject", state_version=created["state_version"],
        )
    assert intruder.value.code == "PAYMENT_REQUEST_FORBIDDEN"


@pytest.mark.asyncio
async def test_failed_payment_keeps_request_and_ledger_unchanged(
    payment_request_store,
) -> None:
    created = await payment_request_service.create_payment_request(
        requester_user_id="U1", payer_user_id="U2", receive_account_id="A1",
        amount=150, note="金额超过余额", idempotency_key="request-key-004",
    )

    with pytest.raises(BankError) as insufficient:
        prepared = await payment_request_service.prepare_payment_request_payment(
            payment_request_id=created["payment_request_id"],
            payer_user_id="U2",
            payer_account_id="A2",
            state_version=created["state_version"],
        )
        async with payment_request_store() as session:
            await submit_transfer(
                session,
                BankTransferCommand(
                    operation_id=prepared["operation_id"],
                    from_account="A2",
                    to_account="A1",
                    amount_fen=15000,
                    currency="CNY",
                    param_digest=parameter_digest("A2", "A1", 15000, "CNY", "金额超过余额"),
                    note="金额超过余额",
                ),
                TrustedExecutionContext(
                    subject_user_id="U2", authorization_id="AUTH2", control_epoch=1
                ),
            )
    assert insufficient.value.code == "INSUFFICIENT_FUNDS"

    requests = await payment_request_service.list_payment_requests(
        user_id="U2", role="incoming"
    )
    async with payment_request_store() as session:
        payer_account = await session.get(AccountORM, "A2")
        receive_account = await session.get(AccountORM, "A1")
        transactions = (await session.execute(select(TransactionORM))).scalars().all()
    assert requests[0]["status"] == "PENDING"
    assert payer_account.balance == 100.0
    assert receive_account.balance == 100.0
    assert transactions == []


@pytest.mark.asyncio
async def test_batch_payment_request_supports_equal_split_and_idempotency(
    payment_request_store,
) -> None:
    created = await payment_request_service.create_payment_request_batch(
        requester_user_id="U1",
        payer_user_ids=["U2", "U3"],
        receive_account_id="A1",
        amount=12.34,
        split_equally=True,
        note="AA 午餐",
        idempotency_key="batch-request-key-001",
    )
    assert len(created) == 2
    assert {item["payer_user_id"] for item in created} == {"U2", "U3"}
    assert {item["amount_fen"] for item in created} == {617}

    duplicate = await payment_request_service.create_payment_request_batch(
        requester_user_id="U1",
        payer_user_ids=["U2", "U3"],
        receive_account_id="A1",
        amount=12.34,
        split_equally=True,
        note="AA 午餐",
        idempotency_key="batch-request-key-001",
    )
    assert [item["payment_request_id"] for item in duplicate] == [
        item["payment_request_id"] for item in created
    ]

    with pytest.raises(BankError) as uneven:
        await payment_request_service.create_payment_request_batch(
            requester_user_id="U1",
            payer_user_ids=["U2", "U3"],
            receive_account_id="A1",
            amount=10.01,
            split_equally=True,
            note=None,
            idempotency_key="batch-request-key-002",
        )
    assert uneven.value.code == "AMOUNT_NOT_EVENLY_DIVISIBLE"
