"""卡片管理：银行事实、黄色确认、红色确认+TOTP、版本与幂等边界。"""

from datetime import datetime, timedelta, timezone

import httpx
import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, AuditLogORM, Base, CardORM, CardOperationORM, UserORM
from backend.app.services.bank import card_service


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([
            UserORM(user_id="U1", name="甲", phone="13800000001", id_card="demo", password_hash="hash", status="active"),
            UserORM(user_id="U2", name="乙", phone="13800000002", id_card="demo", password_hash="hash", status="active"),
        ])
        await session.flush()
        session.add_all([
            AccountORM(account_id="A1", user_id="U1", account_type="savings", card_number="6222****0001", balance_fen=100000),
            AccountORM(account_id="A2", user_id="U1", account_type="credit", card_number="6225****0002", balance_fen=0),
            AccountORM(account_id="A3", user_id="U2", account_type="savings", card_number="6222****0003", balance_fen=100000),
        ])
        await session.flush()
        session.add_all([
            CardORM(card_id="C1", user_id="U1", account_id="A1", card_type="DEBIT", is_virtual=False,
                    card_number="6222****0001", status="ACTIVE", single_txn_limit_fen=1_000_000,
                    daily_txn_limit_fen=5_000_000, single_txn_cap_fen=5_000_000, daily_txn_cap_fen=10_000_000),
            CardORM(card_id="C2", user_id="U1", account_id="A2", card_type="CREDIT", is_virtual=False,
                    card_number="6225****0002", status="LOCKED", lock_reason="风控锁定",
                    credit_limit_fen=2_000_000, credit_limit_cap_fen=5_000_000, single_txn_limit_fen=500_000,
                    daily_txn_limit_fen=2_000_000, single_txn_cap_fen=2_000_000, daily_txn_cap_fen=5_000_000),
            CardORM(card_id="C3", user_id="U2", account_id="A3", card_type="DEBIT", is_virtual=False,
                    card_number="6222****0003", status="ACTIVE", single_txn_limit_fen=1_000_000,
                    daily_txn_limit_fen=5_000_000, single_txn_cap_fen=5_000_000, daily_txn_cap_fen=10_000_000),
        ])
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from backend.app.database.connection import get_db
    from backend.app.main import app

    async def override():
        yield db
    app.dependency_overrides[get_db] = override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db, None)


def headers(user_id: str = "U1") -> dict:
    from backend.app.middleware.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token({"sub": user_id})}


async def preview(client, operation_id: str, user_id: str = "U1", **body) -> httpx.Response:
    return await client.post("/api/v1/cards/operations", headers=headers(user_id),
                             json={"operation_id": operation_id, **body})


async def enroll_totp(db, user_id: str = "U1") -> str:
    secret = pyotp.random_base32()
    user = await db.get(UserORM, user_id)
    user.totp_secret, user.totp_enabled = secret, True
    await db.commit()
    return secret


async def card(db, card_id: str) -> CardORM:
    return (await db.execute(select(CardORM).where(CardORM.card_id == card_id)
                             .execution_options(populate_existing=True))).scalar_one()


@pytest.mark.asyncio
async def test_list_is_scoped_and_requires_authentication(client) -> None:
    assert (await client.get("/api/v1/cards")).status_code == 401
    response = await client.get("/api/v1/cards", headers=headers())
    assert response.status_code == 200
    assert [item["card_id"] for item in response.json()] == ["C1", "C2"]
    assert response.json()[1]["credit_limit_fen"] == 2_000_000
    response = await preview(client, "op-foreign-card", action="card_report_lost", card_id="C3")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CARD_NOT_FOUND"


@pytest.mark.asyncio
async def test_permission_levels_follow_shared_permission_map(client) -> None:
    cases = [
        ({"action": "card_apply_virtual", "account_id": "A1"}, "yellow"),
        ({"action": "card_set_transaction_limits", "card_id": "C1",
          "single_txn_limit": 2000, "daily_txn_limit": 8000}, "yellow"),
        ({"action": "card_unlock", "card_id": "C2"}, "red"),
        ({"action": "card_report_lost", "card_id": "C1"}, "red"),
    ]
    for index, (body, level) in enumerate(cases):
        response = await preview(client, f"op-level-{index}", **body)
        assert response.status_code == 201, response.text
        view = response.json()
        assert view["permission_level"] == level
        assert view["requires_confirmation"] is True
        assert view["requires_mfa"] is (level == "red")
        assert view["status"] == "AWAITING_CONFIRMATION"


@pytest.mark.asyncio
async def test_virtual_card_application_needs_confirmation_and_uses_bank_product(client, db) -> None:
    response = await preview(client, "op-apply-1", action="card_apply_virtual", account_id="A1")
    view = response.json()
    assert [c["after"] for c in view["changes"]] == [
        "6222****0001", card_service.VIRTUAL_CARD_PRODUCT["single_txn_limit_fen"],
        card_service.VIRTUAL_CARD_PRODUCT["daily_txn_limit_fen"]]
    assert await db.scalar(select(func.count()).select_from(CardORM).where(CardORM.is_virtual.is_(True))) == 0

    wrong = await client.post("/api/v1/cards/operations/op-apply-1/confirm", headers=headers(),
                              json={"digest": "0" * 64})
    assert wrong.json()["detail"]["code"] == "DIGEST_MISMATCH"

    done = await client.post("/api/v1/cards/operations/op-apply-1/confirm", headers=headers(),
                             json={"digest": view["digest"]})
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["status"] == "COMPLETED"
    assert result["result"]["is_virtual"] is True
    assert result["result"]["account_id"] == "A1"
    replay = await client.post("/api/v1/cards/operations/op-apply-1/confirm", headers=headers(),
                               json={"digest": view["digest"]})
    assert replay.json()["result"]["card_id"] == result["result"]["card_id"]
    assert await db.scalar(select(func.count()).select_from(CardORM).where(CardORM.is_virtual.is_(True))) == 1

    credit = await preview(client, "op-apply-credit", action="card_apply_virtual", account_id="A2")
    assert credit.json()["detail"]["code"] == "ACCOUNT_TYPE_NOT_ELIGIBLE"
    foreign = await preview(client, "op-apply-foreign", action="card_apply_virtual", account_id="A3")
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_virtual_card_count_is_limited_by_bank_rule(client) -> None:
    for index in range(card_service.MAX_VIRTUAL_CARDS_PER_ACCOUNT):
        view = (await preview(client, f"op-apply-n{index}", action="card_apply_virtual", account_id="A1")).json()
        done = await client.post(f"/api/v1/cards/operations/op-apply-n{index}/confirm", headers=headers(),
                                 json={"digest": view["digest"]})
        assert done.json()["status"] == "COMPLETED"
    response = await preview(client, "op-apply-over", action="card_apply_virtual", account_id="A1")
    assert response.json()["detail"]["code"] == "VIRTUAL_CARD_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_transaction_limits_validate_against_card_caps(client, db) -> None:
    body = {"action": "card_set_transaction_limits", "card_id": "C1"}
    over = await preview(client, "op-limit-over", **body, single_txn_limit=50000.01, daily_txn_limit=60000)
    assert over.json()["detail"]["code"] == "TXN_LIMIT_OUT_OF_RANGE"
    inverted = await preview(client, "op-limit-inv", **body, single_txn_limit=9000, daily_txn_limit=8000)
    assert inverted.json()["detail"]["code"] == "TXN_LIMIT_INCONSISTENT"
    same = await preview(client, "op-limit-same", **body, single_txn_limit=10000, daily_txn_limit=50000)
    assert same.json()["detail"]["code"] == "TXN_LIMIT_UNCHANGED"
    missing = await preview(client, "op-limit-missing", **body, single_txn_limit=100)
    assert missing.status_code == 422
    extra = await preview(client, "op-limit-extra", **body, single_txn_limit=100, daily_txn_limit=200, credit_limit=1)
    assert extra.status_code == 422
    precision = await preview(client, "op-limit-prec", **body, single_txn_limit="1.001", daily_txn_limit=200)
    assert precision.status_code == 422

    view = (await preview(client, "op-limit-ok", **body, single_txn_limit="2000.50", daily_txn_limit=8000)).json()
    assert view["changes"][0] == {"field": "single_txn_limit_fen", "label": "单笔交易限额",
                                  "before": 1_000_000, "after": 200_050, "unit": "fen"}
    assert (await card(db, "C1")).single_txn_limit_fen == 1_000_000
    done = await client.post("/api/v1/cards/operations/op-limit-ok/confirm", headers=headers(),
                             json={"digest": view["digest"]})
    assert done.json()["status"] == "COMPLETED"
    updated = await card(db, "C1")
    assert (updated.single_txn_limit_fen, updated.daily_txn_limit_fen, updated.state_version) == (200_050, 800_000, 2)


@pytest.mark.asyncio
async def test_unlock_then_credit_limit_require_confirmation_and_totp(client, db) -> None:
    locked = await preview(client, "op-credit-locked", action="card_adjust_credit_limit", card_id="C2", credit_limit=30000)
    assert locked.json()["detail"]["code"] == "CARD_NOT_ACTIVE"

    view = (await preview(client, "op-unlock-1", action="card_unlock", card_id="C2")).json()
    confirmed = await client.post("/api/v1/cards/operations/op-unlock-1/confirm", headers=headers(),
                                  json={"digest": view["digest"]})
    assert confirmed.json()["status"] == "AWAITING_MFA"
    assert confirmed.json()["allowed_actions"] == ["verify", "cancel"]
    assert (await card(db, "C2")).status == "LOCKED"

    not_enrolled = await client.post("/api/v1/cards/operations/op-unlock-1/verify", headers=headers(),
                                     json={"proof": "123456"})
    assert not_enrolled.json()["detail"]["code"] == "TOTP_NOT_ENROLLED"
    secret = await enroll_totp(db)
    wrong = await client.post("/api/v1/cards/operations/op-unlock-1/verify", headers=headers(),
                              json={"proof": "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"})
    assert wrong.status_code == 401
    assert (await db.get(CardOperationORM, "op-unlock-1", populate_existing=True)).failed_verifications == 1
    done = await client.post("/api/v1/cards/operations/op-unlock-1/verify", headers=headers(),
                             json={"proof": pyotp.TOTP(secret).now()})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"
    unlocked = await card(db, "C2")
    assert (unlocked.status, unlocked.lock_reason) == ("ACTIVE", None)

    above_cap = await preview(client, "op-credit-cap", action="card_adjust_credit_limit", card_id="C2", credit_limit=50000.01)
    assert above_cap.json()["detail"]["code"] == "CREDIT_LIMIT_OUT_OF_RANGE"
    debit = await preview(client, "op-credit-debit", action="card_adjust_credit_limit", card_id="C1", credit_limit=100)
    assert debit.json()["detail"]["code"] == "NOT_CREDIT_CARD"
    view = (await preview(client, "op-credit-1", action="card_adjust_credit_limit", card_id="C2", credit_limit=30000)).json()
    assert view["requires_mfa"] is True
    assert view["changes"][0]["before"] == 2_000_000 and view["changes"][0]["after"] == 3_000_000
    await client.post("/api/v1/cards/operations/op-credit-1/confirm", headers=headers(), json={"digest": view["digest"]})
    assert (await card(db, "C2")).credit_limit_fen == 2_000_000
    # 同一时间步的验证码不能重放。
    replay = await client.post("/api/v1/cards/operations/op-credit-1/verify", headers=headers(),
                               json={"proof": pyotp.TOTP(secret).now()})
    assert replay.json()["detail"]["code"] == "TOTP_REPLAY"
    user = await db.get(UserORM, "U1")
    user.totp_last_timestep -= 1
    await db.commit()
    done = await client.post("/api/v1/cards/operations/op-credit-1/verify", headers=headers(),
                             json={"proof": pyotp.TOTP(secret).now()})
    assert done.json()["status"] == "COMPLETED"
    assert (await card(db, "C2")).credit_limit_fen == 3_000_000
    audits = (await db.execute(select(AuditLogORM.action, AuditLogORM.permission_level))).all()
    assert ("card_adjust_credit_limit_verify", "red") in audits
    assert ("card_adjust_credit_limit", "delegated") in audits


@pytest.mark.asyncio
async def test_report_lost_is_terminal(client, db) -> None:
    secret = await enroll_totp(db)
    view = (await preview(client, "op-lost-1", action="card_report_lost", card_id="C1")).json()
    await client.post("/api/v1/cards/operations/op-lost-1/confirm", headers=headers(), json={"digest": view["digest"]})
    done = await client.post("/api/v1/cards/operations/op-lost-1/verify", headers=headers(),
                             json={"proof": pyotp.TOTP(secret).now()})
    assert done.json()["result"]["status"] == "LOST"
    assert (await card(db, "C1")).lost_reported_at is not None
    for index, body in enumerate([{"action": "card_unlock", "card_id": "C1"},
                                  {"action": "card_report_lost", "card_id": "C1"},
                                  {"action": "card_set_transaction_limits", "card_id": "C1",
                                   "single_txn_limit": 1, "daily_txn_limit": 2}]):
        response = await preview(client, f"op-after-lost-{index}", **body)
        assert response.json()["detail"]["code"] == "CARD_LOST"


@pytest.mark.asyncio
async def test_stale_preview_is_rejected_by_bank_and_not_retried(client, db) -> None:
    body = {"action": "card_set_transaction_limits", "card_id": "C1"}
    first = (await preview(client, "op-stale-a", **body, single_txn_limit=2000, daily_txn_limit=8000)).json()
    second = (await preview(client, "op-stale-b", **body, single_txn_limit=3000, daily_txn_limit=9000)).json()
    await client.post("/api/v1/cards/operations/op-stale-a/confirm", headers=headers(), json={"digest": first["digest"]})
    stale = await client.post("/api/v1/cards/operations/op-stale-b/confirm", headers=headers(),
                              json={"digest": second["digest"]})
    assert stale.json()["detail"]["code"] == "STALE_STATE_VERSION"
    row = await db.get(CardOperationORM, "op-stale-b", populate_existing=True)
    assert (row.status, row.error_code) == ("FAILED", "STALE_STATE_VERSION")
    assert (await card(db, "C1")).single_txn_limit_fen == 200_000


@pytest.mark.asyncio
async def test_idempotency_ownership_cancel_and_expiry(client, db) -> None:
    body = {"action": "card_set_transaction_limits", "card_id": "C1", "single_txn_limit": 2000, "daily_txn_limit": 8000}
    first = (await preview(client, "op-idem-1", **body)).json()
    assert (await preview(client, "op-idem-1", **body)).json()["digest"] == first["digest"]
    conflict = await preview(client, "op-idem-1", **{**body, "daily_txn_limit": 9000})
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert (await client.get("/api/v1/cards/operations/op-idem-1", headers=headers("U2"))).status_code == 404
    other = await client.post("/api/v1/cards/operations/op-idem-1/confirm", headers=headers("U2"),
                              json={"digest": first["digest"]})
    assert other.status_code == 404

    cancelled = await client.post("/api/v1/cards/operations/op-idem-1/cancel", headers=headers())
    assert cancelled.json()["status"] == "CANCELLED"
    after = await client.post("/api/v1/cards/operations/op-idem-1/confirm", headers=headers(),
                              json={"digest": first["digest"]})
    assert after.json()["detail"]["code"] == "AUTHORIZATION_WRONG_STATE"

    view = (await preview(client, "op-expire-1", **body)).json()
    row = await db.get(CardOperationORM, "op-expire-1")
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    expired = await client.post("/api/v1/cards/operations/op-expire-1/confirm", headers=headers(),
                                json={"digest": view["digest"]})
    assert expired.json()["detail"]["code"] == "AUTHORIZATION_EXPIRED"
    assert (await db.get(CardOperationORM, "op-expire-1", populate_existing=True)).status == "EXPIRED"
    assert (await card(db, "C1")).single_txn_limit_fen == 1_000_000


@pytest.mark.asyncio
async def test_repeated_wrong_codes_terminate_authorization(client, db) -> None:
    secret = await enroll_totp(db)
    view = (await preview(client, "op-brute-1", action="card_report_lost", card_id="C1")).json()
    await client.post("/api/v1/cards/operations/op-brute-1/confirm", headers=headers(), json={"digest": view["digest"]})
    wrong = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"
    for _ in range(4):
        assert (await client.post("/api/v1/cards/operations/op-brute-1/verify", headers=headers(),
                                  json={"proof": wrong})).status_code == 401
    locked = await client.post("/api/v1/cards/operations/op-brute-1/verify", headers=headers(), json={"proof": wrong})
    assert locked.json()["detail"]["code"] == "VERIFICATION_LOCKED"
    correct = await client.post("/api/v1/cards/operations/op-brute-1/verify", headers=headers(),
                                json={"proof": pyotp.TOTP(secret).now()})
    assert correct.json()["detail"]["code"] == "AUTHORIZATION_WRONG_STATE"
    assert (await card(db, "C1")).status == "ACTIVE"
