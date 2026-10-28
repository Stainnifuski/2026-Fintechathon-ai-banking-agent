"""Unified scheduled-task list and owner-scoped custom dispatch."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user
from backend.app.models import Base, UserORM
from backend.app.models.scheduled_task import ScheduledTaskORM
from backend.app.models.workflow import TransferPlanORM, TransferTaskORM
from backend.app.routers.scheduled_tasks import router
from tests.integration.test_task_transfer_flow import db


@pytest.mark.asyncio
async def test_scheduled_tasks_list_transfer_create_custom_and_start_once(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scheduled-tasks.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    async with sessions() as db:
        db.add(UserORM(user_id="U1", name="张三", phone="13800000001", id_card="demo", password_hash="hash", status="active"))
        db.add(TransferTaskORM(task_id="TASK-SCHEDULED", user_id="U1", phase="WAITING_EXECUTION",
                               outcome="NONE", state_version=1, current_plan_version=1))
        db.add(TransferPlanORM(plan_id="PLAN-SCHEDULED", task_id="TASK-SCHEDULED", plan_version=1,
                               is_current=True, params={"scheduled_at": future.isoformat(),
                                                        "recipient_name": "李四", "amount_fen": 2500}))
        await db.commit()
    app = FastAPI()
    app.include_router(router)
    async def db_override():
        async with sessions() as db:
            yield db
    owner = {"user_id": "U1"}
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = lambda: owner
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = (await client.get("/api/v1/scheduled-tasks")).json()["items"]
        assert len(listed) == 1
        assert listed[0]["task_type"] == "transfer"
        assert "李四" in listed[0]["description"]
        assert listed[0]["can_start"] is False
        assert listed[0]["completed"] is False
        created = (await client.post("/api/v1/scheduled-tasks", json={
            "description": " 查询本月支出并制定预算 ", "scheduled_at": future.isoformat()}))
        assert created.status_code == 201
        task = created.json()
        assert task["description"] == "查询本月支出并制定预算"
        assert task["status"] == "SCHEDULED" and not task["can_start"]
        assert (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/start")).status_code == 409
        async with sessions() as db:
            stored = await db.get(ScheduledTaskORM, task["task_id"])
            assert "查询本月支出" not in stored.description_ciphertext
        assert (await client.post("/api/v1/scheduled-tasks", json={
            "description": "无效时区", "scheduled_at": future.replace(tzinfo=None).isoformat()})).status_code == 422
        assert (await client.post("/api/v1/scheduled-tasks", json={
            "description": "过去的时间", "scheduled_at": (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()})).status_code == 422
        owner["user_id"] = "U2"
        assert (await client.get("/api/v1/scheduled-tasks")).json()["items"] == []
        assert (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/start")).status_code == 404
        owner["user_id"] = "U1"
        async with sessions() as db:
            row = await db.get(ScheduledTaskORM, task["task_id"])
            row.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        ready = (await client.get("/api/v1/scheduled-tasks")).json()["items"]
        assert next(item for item in ready if item["task_id"] == task["task_id"])["can_start"]
        started = (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/start")).json()
        repeated = (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/start")).json()
        assert started["status"] == repeated["status"] == "STARTED"
        assert started["dispatch_request_id"] == repeated["dispatch_request_id"]
        assert started["started_at"] is not None
        assert started["completed"] is True
        later = (await client.post("/api/v1/scheduled-tasks", json={
            "description": "稍后处理", "scheduled_at": (future + timedelta(minutes=5)).isoformat()})).json()
        async with sessions() as db:
            db.add(TransferTaskORM(task_id="TASK-FINISHED", user_id="U1", phase="FINISHED",
                                   outcome="SUCCEEDED", state_version=1, current_plan_version=1))
            db.add(TransferPlanORM(plan_id="PLAN-FINISHED", task_id="TASK-FINISHED", plan_version=1,
                                   is_current=True, params={"scheduled_at": (future - timedelta(minutes=5)).isoformat(),
                                                            "recipient_name": "王五", "amount_fen": 1000}))
            await db.commit()
        grouped = (await client.get("/api/v1/scheduled-tasks")).json()["items"]
        assert [item["task_id"] for item in grouped] == ["TASK-SCHEDULED", later["task_id"],
                                                       task["task_id"], "TASK-FINISHED"]
        assert [item["completed"] for item in grouped] == [False, False, True, True]
    await engine.dispose()


@pytest.mark.asyncio
async def test_custom_task_locks_cash_spent_only_by_its_conversation_and_returns_rest(db):
    """自定义任务可锁定活期资金；开始后只有该任务对话里的付款能用，结束后剩余退回。"""
    from tests.integration.test_task_transfer_flow import InProcessBankApi
    from backend.app.models import AccountORM
    from backend.app.services.ai.scheduled_funding import bind_dispatch_session, fund_transfer_task
    from backend.app.services.bank.available_funds import available_cash, held_cash
    from backend.app.services.orchestration import accept_task, confirm_task, create_transfer_task
    (await db.get(AccountORM, "A1")).balance_fen = 100000
    await db.commit()
    app = FastAPI()
    app.include_router(router)
    async def use_db():
        yield db
    app.dependency_overrides[get_db] = use_db
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    bank = InProcessBankApi(db)

    async def pay(suffix):
        task = await create_transfer_task(db, bank, user_id="U1", request_id=f"fund-create-{suffix}",
                                          operation_id=f"fund-operation-{suffix}", from_account="A1",
                                          to_account="A2", amount=500.0)
        if suffix == "in-task":
            await fund_transfer_task(db, "U1", "NOVA-task-chat", task["task_id"])
            await db.commit()
        ready = await confirm_task(db, task_id=task["task_id"], user_id="U1", request_id=f"fund-confirm-{suffix}",
                                   state_version=task["state_version"], plan_version=task["plan_version"], bank=bank)
        return await accept_task(db, bank, task_id=task["task_id"], user_id="U1",
                                 request_id=f"fund-accept-{suffix}", state_version=ready["state_version"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/scheduled-tasks", json={
            "description": "给李四转500元", "scheduled_at": future.isoformat(),
            "lock_amount": "600", "lock_account_id": "A1"})
        assert created.status_code == 201, created.text
        task = created.json()
        assert task["lock"]["amount_fen"] == 60000 and task["lock"]["remaining_fen"] == 60000
        assert await held_cash(db, "A1") == 60000
        short = await client.post("/api/v1/scheduled-tasks", json={
            "description": "再锁一笔", "scheduled_at": future.isoformat(), "lock_amount": "500", "lock_account_id": "A1"})
        assert short.status_code == 409 and short.json()["detail"]["code"] == "INSUFFICIENT_FUNDS"
        assert len((await client.get("/api/v1/scheduled-tasks")).json()["items"]) == 1
        foreign = await client.post("/api/v1/scheduled-tasks", json={
            "description": "锁别人的钱", "scheduled_at": future.isoformat(), "lock_amount": "1", "lock_account_id": "A2"})
        assert foreign.status_code == 404

        # Before starting, locked money cannot be spent even by the task's own conversation.
        assert (await pay("before-start"))["outcome"] == "FAILED"
        stored = await db.get(ScheduledTaskORM, task["task_id"])
        stored.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        started = (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/start")).json()
        assert started["status"] == "RUNNING" and started["can_finish"] and not started["completed"]
        await bind_dispatch_session(db, "U1", started["dispatch_request_id"], "NOVA-task-chat")

        # Free cash is 400; the task's own payment of 500 draws 500 from the lock.
        assert (await pay("in-task"))["outcome"] == "SUCCEEDED"
        account = await db.get(AccountORM, "A1", populate_existing=True)
        assert account.balance_fen == 50000 and await held_cash(db, "A1") == 10000
        # A payment outside the task still cannot touch the remaining lock.
        assert (await pay("outside"))["outcome"] == "FAILED"

        finished = (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/finish")).json()
        assert finished["status"] == "FINISHED" and finished["completed"]
        assert finished["lock"]["used_fen"] == 50000 and finished["lock"]["returned_fen"] == 10000
        assert await held_cash(db, "A1") == 0
        assert await available_cash(db, "A1", account.balance_fen) == 50000
        assert (await client.post(f"/api/v1/scheduled-tasks/{task['task_id']}/cancel")).json()["status"] == "FINISHED"

        # Cancelling before the run returns the whole lock.
        pending = (await client.post("/api/v1/scheduled-tasks", json={
            "description": "备用金", "scheduled_at": future.isoformat(), "lock_amount": "300", "lock_account_id": "A1"})).json()
        assert await held_cash(db, "A1") == 30000
        cancelled = (await client.post(f"/api/v1/scheduled-tasks/{pending['task_id']}/cancel")).json()
        assert cancelled["status"] == "CANCELLED" and cancelled["lock"]["returned_fen"] == 30000
        assert await held_cash(db, "A1") == 0


@pytest.mark.asyncio
async def test_nova_creates_scheduled_task_directly_or_opens_prefilled_form(db):
    import json
    from dataclasses import replace
    from backend.app.models import AccountORM
    from backend.app.schemas.nova import PageContext
    from backend.app.services.ai import nova
    from backend.app.services.ai.nova_tools import ToolContext
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.bank import nova_conversations
    from backend.app.services.bank.available_funds import held_cash
    (await db.get(AccountORM, "A1")).balance_fen = 100000
    await db.commit()
    session_id = (await nova_conversations.create(db, "U1"))["session_id"]
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).astimezone(timezone(timedelta(hours=8)))
    day = tomorrow.date().isoformat()

    def call(args, request_id):
        ctx = ToolContext("U1", db, PageContext(), session_id=session_id, request_id=request_id, original_message="x")
        return nova.execute_tool({"function": {"name": "manage_scheduled_task", "arguments": json.dumps(args)}}, ctx)

    created = await call({"description": "查询本月支出并帮我制定预算", "run_date": day, "run_time": "09:00",
                          "lock_amount": "300"}, "R1")
    assert created["data"]["created"] and created["data"]["task"]["lock_amount_fen"] == 30000
    assert await held_cash(db, "A1") == 30000

    missing = await call({"description": "提醒我交房租"}, "R2")
    assert missing["data"]["form_opened"] and missing["data"]["missing_fields"] == ["执行日期", "执行时间"]
    past = await call({"description": "跑步", "run_date": "2020-01-01", "run_time": "07:00"}, "R3")
    assert "已经过去" in past["data"]["notice"]
    short = await call({"description": "买理财", "run_date": day, "run_time": "10:00", "lock_amount": "900"}, "R4")
    assert short["data"]["form_opened"] and "余额不足" in short["data"]["notice"]
    assert await held_cash(db, "A1") == 30000

    listed = await nova.execute_tool({"function": {"name": "list_scheduled_tasks", "arguments": "{}"}},
                                     ToolContext("U1", db, PageContext()))
    task_id = listed["data"]["items"][0]["task_id"]
    shown = await call({"action": "show", "task_id": task_id}, "R5")
    assert shown["data"]["task"]["task_id"] == task_id
    cards = (await get_session(db, "U1", session_id))["scheduled_tasks"]
    assert [bool(card["task"]) for card in cards] == [False, False, False, True]
    assert cards[0]["prefill"] == {"description": "提醒我交房租"}
    assert cards[2]["prefill"]["lock_account_id"] == "A1"
    assert "定时任务业务 Skill" in nova.SYSTEM_PROMPT
