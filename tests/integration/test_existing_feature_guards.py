"""Server guards for existing scheduled transfers and trusted subscription cancellation."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import select, func

from tests.integration.test_task_transfer_flow import db, InProcessBankApi
from tests.unit.test_subscriptions import subscription_store
from backend.app.models import AccountORM, SubscriptionORM, TransactionORM
from backend.app.middleware.auth import get_current_user, verify_token
from backend.app.routers.subscriptions import router as subscriptions_router
from backend.app.routers.tasks import router as tasks_router
from backend.app.database.connection import get_db
from backend.app.integrations.bank_api import get_bank_api
from backend.app.services.orchestration import create_transfer_task, confirm_task, accept_task, TaskError
from backend.app.services.orchestration.scheduler import execute_due_scheduled_transfers
from backend.app.services.bank import subscription_service as subscriptions
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
async def test_early_http_accept_rejected_and_due_scheduler_still_executes(db):
    bank = InProcessBankApi(db)
    due = datetime.now(timezone.utc) + timedelta(days=1)
    task = await create_transfer_task(db, bank, user_id="U1", request_id="guard-create-early",
        operation_id="guard-operation-early", from_account="A1", to_account="A2", amount=100, scheduled_at=due)
    waiting = await confirm_task(db, task_id=task["task_id"], user_id="U1", request_id="guard-confirm-early", bank=bank,
        state_version=task["state_version"], plan_version=task["plan_version"])
    app = FastAPI()
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id":"U1"}
    app.dependency_overrides[get_bank_api] = lambda: bank
    async def use_db():
        yield db
    app.dependency_overrides[get_db] = use_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/tasks/{task['task_id']}/accept", json={
            "request_id":"guard-accept-early", "state_version":waiting["state_version"],
            "execution_time":due.isoformat()})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SCHEDULE_NOT_DUE"
        # Extra HTTP clock fields cannot override the trusted service clock.
        assert (await db.get(AccountORM,"A1")).balance == 5000
        assert await execute_due_scheduled_transfers(db, bank, now=due-timedelta(seconds=1)) == 0
        assert await execute_due_scheduled_transfers(db, bank, now=due) == 1
        assert (await db.get(AccountORM,"A1")).balance == 4900
        assert await execute_due_scheduled_transfers(db, bank, now=due+timedelta(seconds=1)) == 0


@pytest.mark.asyncio
async def test_cancel_http_requires_bound_preview_and_preserves_idempotency(subscription_store):
    app = FastAPI()
    app.include_router(subscriptions_router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id":"U1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/subscriptions/SUB1/cancel",json={"state_version":1})
        assert response.status_code == 422
        preview = (await client.post("/api/v1/subscriptions/SUB1/cancel-preview")).json()
        assert preview["subscription"]["status"] == "ACTIVE"
        assert verify_token(preview["confirmation_token"]) is None
        async with subscription_store() as session:
            assert (await session.get(SubscriptionORM,"SUB1")).status == "ACTIVE"
            assert (await session.get(AccountORM,"A1")).balance == 100
        request = {"state_version":1,"confirmation_token":preview["confirmation_token"]}
        for _ in range(2):
            response = await client.post("/api/v1/subscriptions/SUB1/cancel",json=request)
            assert response.status_code == 200
            assert response.json()["status"] == "CANCELLED"
            assert response.json()["state_version"] == 2
        async with subscription_store() as session:
            assert (await session.get(AccountORM,"A1")).balance == 100
            assert await session.scalar(select(func.count()).select_from(TransactionORM)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "tampered", "expired", "wrong_user", "wrong_subscription", "wrong_version", "changed_facts"])
async def test_cancel_rejects_invalid_confirmation_without_mutation(subscription_store, case):
    preview = await subscriptions.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
    token = preview["confirmation_token"]
    version = 1
    if case == "missing":
        token = None
    elif case == "tampered":
        token = "invalid." + token
    elif case in {"expired", "wrong_user", "wrong_subscription"}:
        claims = jwt.decode(token, subscriptions._confirmation_key(), algorithms=["HS256"])
        if case == "expired": claims["exp"] = int((datetime.now(timezone.utc)-timedelta(seconds=10)).timestamp())
        elif case == "wrong_user": claims["user_id"] = "U2"
        else: claims["subscription_id"] = "SUB_OTHER"
        token = jwt.encode(claims, subscriptions._confirmation_key(), algorithm="HS256")
    elif case == "wrong_version":
        version = 2
    else:
        async with subscription_store() as session:
            sub = await session.get(SubscriptionORM,"SUB1")
            sub.amount_fen = 1600
            await session.commit()
    with pytest.raises(BankError) as rejected:
        await subscriptions.cancel_subscription(subscription_id="SUB1",user_id="U1",state_version=version,confirmation_token=token)
    assert rejected.value.code in {"CONFIRMATION_REQUIRED", "CONFIRMATION_INVALID"}
    async with subscription_store() as session:
        sub = await session.get(SubscriptionORM,"SUB1")
        assert sub.status == "ACTIVE" and sub.state_version == 1
        assert (await session.get(AccountORM,"A1")).balance == 100


@pytest.mark.asyncio
async def test_preview_owner_isolation_and_post_charge_old_confirmation_rejected(subscription_store):
    with pytest.raises(BankError) as rejected:
        await subscriptions.preview_subscription_cancellation(subscription_id="SUB1",user_id="U2")
    assert rejected.value.code == "SUBSCRIPTION_NOT_FOUND"
    preview = await subscriptions.preview_subscription_cancellation(subscription_id="SUB1",user_id="U1")
    await subscriptions.simulate_subscription_charge(subscription_id="SUB1",user_id="U1",state_version=1,idempotency_key="guard-charge-version")
    with pytest.raises(BankError):
        await subscriptions.cancel_subscription(subscription_id="SUB1",user_id="U1",state_version=1,confirmation_token=preview["confirmation_token"])
    fresh = await subscriptions.preview_subscription_cancellation(subscription_id="SUB1",user_id="U1")
    result = await subscriptions.cancel_subscription(subscription_id="SUB1",user_id="U1",state_version=2,confirmation_token=fresh["confirmation_token"])
    assert result["status"] == "CANCELLED"
