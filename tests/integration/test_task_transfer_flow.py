"""应用任务与模拟银行分层的集成测试。"""

from datetime import datetime, timedelta, timezone

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
    release_payment_request_payment,
    resolve_transfer_parties,
    submit_transfer,
    validate_transfer_parties,
    verify_sms_for_operation,
    verify_totp_for_operation,
)
from backend.app.services.orchestration import (
    TaskError,
    accept_task,
    cancel_task,
    confirm_task,
    create_transfer_task,
    reconcile_task,
    verify_task,
)
from backend.app.services.orchestration.scheduler import execute_due_scheduled_transfers


class InProcessBankApi:
    """测试适配器：保持与生产 HTTP 客户端相同的接口边界。"""

    def __init__(self, db) -> None:
        self.db = db

    async def verify_totp(self, *, subject_user_id: str, proof: str) -> None:
        try:
            await verify_totp_for_operation(self.db, subject_user_id, proof)
            await self.db.commit()
        except BankError as error:
            raise BankApiError(error.message, error.code, error.status_code) from error

    async def verify_sms(self, *, subject_user_id: str, proof: str, challenge_id: str, reference_id: str) -> None:
        try:
            await verify_sms_for_operation(self.db, subject_user_id, proof, challenge_id=challenge_id,
                                           purpose="transfer", reference_id=reference_id)
            await self.db.commit()
        except BankError as error:
            raise BankApiError(error.message, error.code, error.status_code) from error

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

    async def release_payment_request(self, **kwargs) -> None:
        await release_payment_request_payment(self.db, **kwargs)


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


@pytest.mark.asyncio
async def test_high_risk_transfer_requires_totp_verification(db) -> None:
    import pyotp

    bank = InProcessBankApi(db)
    task = await create_transfer_task(
        db,
        bank,
        user_id="U1",
        request_id="request-create-mfa",
        operation_id="operation-mfa",
        from_account="A1",
        to_account="A2",
        amount=1500.0,
    )
    waiting = await confirm_task(
        db,
        task_id=task["task_id"],
        user_id="U1",
        request_id="request-confirm-mfa",
        state_version=task["state_version"],
        plan_version=task["plan_version"],
    )
    assert waiting["phase"] == "WAITING_VERIFICATION"
    assert (await db.get(AccountORM, "A1")).balance == 5000.0

    # 未绑定验证器时，强验证被拒绝，资金不动。
    with pytest.raises(TaskError):
        await verify_task(
            db,
            bank,
            task_id=task["task_id"],
            user_id="U1",
            request_id="request-verify-noenroll",
            state_version=waiting["state_version"],
            challenge_id="totp",
            proof="000000",
        )
    await db.rollback()

    # 为用户绑定 TOTP 验证器。
    secret = pyotp.random_base32()
    user = await db.get(UserORM, "U1")
    user.totp_secret = secret
    user.totp_enabled = True
    await db.commit()

    # 错误验证码同样被拒绝。
    with pytest.raises(TaskError):
        await verify_task(
            db,
            bank,
            task_id=task["task_id"],
            user_id="U1",
            request_id="request-verify-wrong",
            state_version=waiting["state_version"],
            challenge_id="totp",
            proof="000000",
        )
    await db.rollback()
    assert (await db.get(AccountORM, "A1")).balance == 5000.0

    # 正确的 TOTP 验证码通过强验证，授权生效。
    ready = await verify_task(
        db,
        bank,
        task_id=task["task_id"],
        user_id="U1",
        request_id="request-verify-mfa",
        state_version=waiting["state_version"],
        challenge_id="totp",
        proof=pyotp.TOTP(secret).now(),
    )
    assert ready["phase"] == "READY"
    assert ready["authorization"]["status"] == "VALID"

    executed = await accept_task(
        db,
        bank,
        task_id=task["task_id"],
        user_id="U1",
        request_id="request-accept-mfa",
        state_version=ready["state_version"],
    )
    assert executed["outcome"] == "SUCCEEDED"
    assert (await db.get(AccountORM, "A1")).balance == 3500.0
    assert (await db.get(AccountORM, "A2")).balance == 2500.0


@pytest.mark.asyncio
async def test_high_risk_transfer_accepts_sms_code_bound_to_task(db) -> None:
    import re
    from backend.app.security import sms
    from backend.app.services.bank import send_sms_challenge

    provider = sms.MockSmsProvider()
    sms.set_sms_provider(provider)
    try:
        bank = InProcessBankApi(db)
        task = await create_transfer_task(
            db, bank, user_id="U1", request_id="request-create-sms", operation_id="operation-sms",
            from_account="A1", to_account="A2", amount=1500.0,
        )
        waiting = await confirm_task(
            db, task_id=task["task_id"], user_id="U1", request_id="request-confirm-sms",
            state_version=task["state_version"], plan_version=task["plan_version"],
        )
        assert waiting["phase"] == "WAITING_VERIFICATION"

        # 为其他任务获取的短信码不能用于本任务。
        foreign = await send_sms_challenge(db, "U1", "transfer", "another-task")
        foreign_code = re.search(r"验证码 (\d{6})", provider.messages_for("13800000001", 1)[0].content).group(1)
        with pytest.raises(TaskError):
            await verify_task(
                db, bank, task_id=task["task_id"], user_id="U1", request_id="request-verify-sms-foreign",
                state_version=waiting["state_version"], challenge_id=foreign["challenge_id"],
                proof=foreign_code, method="sms",
            )
        await db.rollback()

        sent = await send_sms_challenge(db, "U1", "transfer", task["task_id"])
        code = re.search(r"验证码 (\d{6})", provider.messages_for("13800000001", 1)[0].content).group(1)
        ready = await verify_task(
            db, bank, task_id=task["task_id"], user_id="U1", request_id="request-verify-sms",
            state_version=waiting["state_version"], challenge_id=sent["challenge_id"],
            proof=code, method="sms",
        )
        assert ready["phase"] == "READY"
        assert ready["authorization"]["status"] == "VALID"
        executed = await accept_task(
            db, bank, task_id=task["task_id"], user_id="U1", request_id="request-accept-sms",
            state_version=ready["state_version"],
        )
        assert executed["outcome"] == "SUCCEEDED"
        assert (await db.get(AccountORM, "A1")).balance == 3500.0
    finally:
        sms.set_sms_provider(None)


@pytest.mark.asyncio
async def test_scheduled_transfer_waits_then_executes_once(db) -> None:
    bank = InProcessBankApi(db)
    execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    task = await create_transfer_task(
        db,
        bank,
        user_id="U1",
        request_id="request-create-scheduled",
        operation_id="operation-scheduled",
        from_account="A1",
        to_account="A2",
        amount=125.0,
        scheduled_at=execute_at,
    )
    waiting = await confirm_task(
        db,
        task_id=task["task_id"],
        user_id="U1",
        request_id="request-confirm-scheduled",
        state_version=task["state_version"],
        plan_version=task["plan_version"],
    )

    assert waiting["phase"] == "WAITING_EXECUTION"
    assert waiting["allowed_actions"] == ["modify", "cancel"]
    assert waiting["scheduled_at"] == execute_at.isoformat()
    assert (await db.get(AccountORM, "A1")).balance == 5000.0

    early_count = await execute_due_scheduled_transfers(
        db, bank, now=execute_at - timedelta(seconds=1)
    )
    assert early_count == 0
    assert (await db.get(AccountORM, "A1")).balance == 5000.0

    due_count = await execute_due_scheduled_transfers(
        db, bank, now=execute_at + timedelta(seconds=1)
    )
    assert due_count == 1
    assert (await db.get(AccountORM, "A1")).balance == 4875.0
    assert (await db.get(AccountORM, "A2")).balance == 1125.0

    duplicate_count = await execute_due_scheduled_transfers(
        db, bank, now=execute_at + timedelta(minutes=1)
    )
    assert duplicate_count == 0
    assert (await db.get(AccountORM, "A1")).balance == 4875.0


@pytest.mark.asyncio
async def test_cancelled_scheduled_transfer_never_executes(db) -> None:
    bank = InProcessBankApi(db)
    execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    task = await create_transfer_task(
        db,
        bank,
        user_id="U1",
        request_id="request-create-scheduled-cancel",
        operation_id="operation-scheduled-cancel",
        from_account="A1",
        to_account="A2",
        amount=80.0,
        scheduled_at=execute_at,
    )
    waiting = await confirm_task(
        db,
        task_id=task["task_id"],
        user_id="U1",
        request_id="request-confirm-scheduled-cancel",
        state_version=task["state_version"],
        plan_version=task["plan_version"],
    )
    cancelled = await cancel_task(
        db,
        bank,
        task_id=task["task_id"],
        user_id="U1",
        request_id="request-cancel-scheduled",
        state_version=waiting["state_version"],
    )

    assert cancelled["phase"] == "FINISHED"
    assert cancelled["outcome"] == "CANCELLED"
    executed = await execute_due_scheduled_transfers(
        db, bank, now=execute_at + timedelta(minutes=1)
    )
    assert executed == 0
    assert (await db.get(AccountORM, "A1")).balance == 5000.0
    assert (await db.get(AccountORM, "A2")).balance == 1000.0
