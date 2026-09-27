"""同一用户跨任务并发受理的风险占用回归。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, TransactionORM, UserORM
from backend.app.services.orchestration import (
    TaskError, accept_task, confirm_task, create_transfer_task,
)
from tests.integration.test_task_transfer_flow import InProcessBankApi, db


@pytest.mark.asyncio
async def test_two_yellow_prechecks_cannot_both_debit(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'risk-race.db'}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            UserORM(user_id="U1", name="甲", phone="13800000001",
                    id_card="demo", password_hash="x", status="active"),
            UserORM(user_id="U2", name="乙", phone="13800000002",
                    id_card="demo", password_hash="x", status="active"),
            AccountORM(account_id="A1", user_id="U1", account_type="savings",
                       card_number="1", balance=1000, daily_transfer_used=0,
                       daily_transfer_limit=50000, status="active"),
            AccountORM(account_id="A3", user_id="U1", account_type="checking",
                       card_number="3", balance=1000, daily_transfer_used=0,
                       daily_transfer_limit=50000, status="active"),
            AccountORM(account_id="A2", user_id="U2", account_type="savings",
                       card_number="2", balance=0, daily_transfer_used=0,
                       daily_transfer_limit=50000, status="active"),
        ])
        await session.commit()

    ready_tasks = []
    for from_account in ("A1", "A3"):
        async with factory() as session:
            bank = InProcessBankApi(session)
            plan = await create_transfer_task(
                session, bank, user_id="U1", request_id=str(uuid4()),
                operation_id=str(uuid4()), from_account=from_account,
                to_account="A2", amount=600,
            )
            assert plan["authorization"]["risk_level"] == "YELLOW"
            ready = await confirm_task(
                session, task_id=plan["task_id"], user_id="U1",
                request_id=str(uuid4()), state_version=plan["state_version"],
                plan_version=plan["plan_version"],
            )
            ready_tasks.append(ready)

    async def accept(ready):
        async with factory() as session:
            try:
                return await accept_task(
                    session, InProcessBankApi(session), task_id=ready["task_id"],
                    user_id="U1", request_id=str(uuid4()),
                    state_version=ready["state_version"],
                )
            except TaskError as error:
                return error.code

    results = await asyncio.gather(*(accept(item) for item in ready_tasks))
    async with factory() as session:
        credited = (await session.get(AccountORM, "A2")).balance
        debits = await session.scalar(select(func.count()).select_from(
            TransactionORM
        ).where(TransactionORM.type == "transfer_out"))
    assert credited <= 600
    assert debits <= 1
    assert any(value == "VERIFICATION_REQUIRED" for value in results)
    await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_risk_decision_fails_closed(db, monkeypatch):
    from backend.app.services.orchestration import task_service
    async def invalid_risk(*args, **kwargs):
        return "GREEN"

    monkeypatch.setattr(task_service, "precheck_risk", invalid_risk)
    with pytest.raises(TaskError) as error:
        await create_transfer_task(
            db, InProcessBankApi(db), user_id="U1",
            request_id=str(uuid4()), operation_id=str(uuid4()),
            from_account="A1", to_account="A2", amount=1,
        )
    assert error.value.code == "RISK_DECISION_INVALID"