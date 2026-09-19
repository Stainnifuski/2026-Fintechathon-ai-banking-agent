"""应用任务与模拟银行分层的集成测试。"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, UserORM
from backend.app.integrations.bank_api import (
    BankApiError,
    BankOperationResult,
    BankResponseUnknown,
    ResolvedTransferParties,
    TransferValidation,
)
from backend.app.services.bank import (
    BankError,
    BankResponseLost,
    query_operation,
    resolve_transfer_parties,
    submit_transfer,
    validate_transfer_parties,
)
from backend.app.services.orchestration import (
    accept_task,
    confirm_task,
    create_transfer_task,
    reconcile_task,
)


class InProcessBankApi:
    """测试适配器：保持与生产 HTTP 客户端相同的接口边界。"""

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
        except BankResponseLost as error:
            raise BankResponseUnknown(str(error), "BANK_RESPONSE_LOST") from error
        except BankError as error:
            raise BankApiError(error.message, error.code, error.status_code) from error
        return BankOperationResult(**result.__dict__)

    async def query_operation(self, operation_id: str) -> BankOperationResult | None:
        result = await query_operation(self.db, operation_id)
        return BankOperationResult(**result.__dict__) if result else None


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
    bank = InProcessBankApi(db)
    task = await create_transfer_task(
        db,
        bank,
        user_id="U1",
        request_id=f"request-create-{suffix}",
        operation_id=f"operation-{suffix}",
        from_account="A1",
        to_account="A2",
        amount=amount,
    )
    ready = await confirm_task(
        db,
        task_id=task["task_id"],
        user_id="U1",
        request_id=f"request-confirm-{suffix}",
        state_version=task["state_version"],
        plan_version=task["plan_version"],
    )
    return ready, bank


@pytest.mark.asyncio
async def test_yellow_transfer_happy_path(db) -> None:
    ready, bank = await _ready_task(db, "happy")
    result = await accept_task(
        db,
        bank,
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
    ready, bank = await _ready_task(db, "lost", amount=50.0)
    unknown = await accept_task(
        db,
        bank,
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
        bank,
        task_id=unknown["task_id"],
        user_id="U1",
        request_id="request-reconcile-lost",
        state_version=unknown["state_version"],
    )
    assert recovered["outcome"] == "SUCCEEDED"
    assert recovered["risk_reservation"]["status"] == "SETTLED"
    assert (await db.get(AccountORM, "A1")).balance == 4950.0
