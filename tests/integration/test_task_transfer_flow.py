"""应用任务与模拟银行分层的集成测试。"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, UserORM
from backend.app.services.orchestration import (
    accept_task,
    confirm_task,
    create_transfer_task,
    reconcile_task,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                UserORM(
                    user_id="U1", name="张三", phone="13800000001",
                    id_card="demo", password_hash="hash", status="active",
                ),
                UserORM(
                    user_id="U2", name="李四", phone="13900000002",
                    id_card="demo", password_hash="hash", status="active",
                ),
                AccountORM(
                    account_id="A1", user_id="U1", account_type="savings",
                    card_number="1", balance=5000.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
                AccountORM(
                    account_id="A2", user_id="U2", account_type="savings",
                    card_number="2", balance=1000.0, daily_transfer_used=0.0,
                    daily_transfer_limit=50000.0, status="active",
                ),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


async def _ready_task(db, suffix: str, amount: float = 100.0):
    task = await create_transfer_task(
        db,
        user_id="U1",
        request_id=f"request-create-{suffix}",
        operation_id=f"operation-{suffix}",
        from_account="A1",
        to_account="A2",
        amount=amount,
    )
    return await confirm_task(
        db,
        task_id=task["task_id"],
        user_id="U1",
        request_id=f"request-confirm-{suffix}",
        state_version=task["state_version"],
        plan_version=task["plan_version"],
    )


@pytest.mark.asyncio
async def test_yellow_transfer_happy_path(db) -> None:
    ready = await _ready_task(db, "happy")
    result = await accept_task(
        db,
        task_id=ready["task_id"],
        user_id="U1",
        request_id="request-accept-happy",
        state_version=ready["state_version"],
    )
    assert result["phase"] == "FINISHED"
    assert result["outcome"] == "SUCCEEDED"
    assert result["operation"]["status"] == "SUCCEEDED"
    assert result["operation"]["bank_tx_id"]
    assert (await db.get(AccountORM, "A1")).balance == 4900.0
    assert (await db.get(AccountORM, "A2")).balance == 1100.0


@pytest.mark.asyncio
async def test_lost_response_recovers_by_original_operation_id(db) -> None:
    ready = await _ready_task(db, "lost", amount=50.0)
    unknown = await accept_task(
        db,
        task_id=ready["task_id"],
        user_id="U1",
        request_id="request-accept-lost",
        state_version=ready["state_version"],
        simulate_response_loss=True,
    )
    assert unknown["phase"] == "RECONCILING"
    assert unknown["operation"]["status"] == "UNKNOWN"
    recovered = await reconcile_task(
        db,
        task_id=unknown["task_id"],
        user_id="U1",
        request_id="request-reconcile-lost",
        state_version=unknown["state_version"],
    )
    assert recovered["outcome"] == "SUCCEEDED"
    assert recovered["risk_reservation"]["status"] == "SETTLED"
    assert (await db.get(AccountORM, "A1")).balance == 4950.0
