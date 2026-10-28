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
