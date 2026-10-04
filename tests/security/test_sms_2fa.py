"""短信第二因素：发送、绑定具体操作、过期/重放/暴力猜测防护及卡片红色路径。"""

import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, AuditLogORM, Base, CardORM, SmsChallengeORM, UserORM
from backend.app.security import sms
from backend.app.services.bank import BankError, send_sms_challenge, verify_sms_for_operation


@pytest.fixture(autouse=True)
def provider():
    mock = sms.MockSmsProvider()
    sms.set_sms_provider(mock)
    yield mock
    sms.set_sms_provider(None)


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
        session.add(AccountORM(account_id="A1", user_id="U1", account_type="savings",
                               card_number="6222****0001", balance_fen=100000))
        await session.flush()
        session.add(CardORM(card_id="C1", user_id="U1", account_id="A1", card_type="DEBIT", is_virtual=False,
                            card_number="6222****0001", status="ACTIVE", single_txn_limit_fen=1_000_000,
                            daily_txn_limit_fen=5_000_000, single_txn_cap_fen=5_000_000,
                            daily_txn_cap_fen=10_000_000))
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


def last_code(provider: sms.MockSmsProvider, phone: str = "13800000001") -> str:
    return re.search(r"验证码 (\d{6})", provider.messages_for(phone, 1)[0].content).group(1)


def other(code: str) -> str:
    return "000000" if code != "000000" else "111111"


async def challenge(db, challenge_id: str) -> SmsChallengeORM:
    return await db.get(SmsChallengeORM, challenge_id, populate_existing=True)


@pytest.mark.asyncio
async def test_code_is_sent_to_registered_phone_and_stored_only_as_hash(db, provider) -> None:
    sent = await send_sms_challenge(db, "U1", "card", "op-1")
    assert sent["phone_masked"] == "138****0001"
    assert "code" not in sent
    code = last_code(provider)
    row = await challenge(db, sent["challenge_id"])
    assert code not in row.code_hash and len(row.code_hash) == 64

    await verify_sms_for_operation(db, "U1", code, challenge_id=sent["challenge_id"],
                                   purpose="card", reference_id="op-1")
    await db.commit()
    with pytest.raises(BankError) as replay:
        await verify_sms_for_operation(db, "U1", code, challenge_id=sent["challenge_id"],
                                       purpose="card", reference_id="op-1")
    assert replay.value.code == "SMS_USED"


@pytest.mark.asyncio
async def test_code_is_bound_to_user_and_operation(db, provider) -> None:
    sent = await send_sms_challenge(db, "U1", "card", "op-1")
    code = last_code(provider)
    for user_id, purpose, reference, expected in [
        ("U2", "card", "op-1", "SMS_CHALLENGE_NOT_FOUND"),
        ("U1", "card", "op-2", "SMS_CHALLENGE_MISMATCH"),
        ("U1", "transfer", "op-1", "SMS_CHALLENGE_MISMATCH"),
    ]:
        with pytest.raises(BankError) as error:
            await verify_sms_for_operation(db, user_id, code, challenge_id=sent["challenge_id"],
                                           purpose=purpose, reference_id=reference)
        assert error.value.code == expected
    with pytest.raises(BankError) as missing:
        await verify_sms_for_operation(db, "U1", code, challenge_id=None, purpose="card", reference_id="op-1")
    assert missing.value.code == "SMS_CHALLENGE_REQUIRED"


@pytest.mark.asyncio
async def test_expired_code_is_rejected(db, provider) -> None:
    sent = await send_sms_challenge(db, "U1", "card", "op-1")
    row = await challenge(db, sent["challenge_id"])
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    with pytest.raises(BankError) as error:
        await verify_sms_for_operation(db, "U1", last_code(provider), challenge_id=sent["challenge_id"],
                                       purpose="card", reference_id="op-1")
    assert error.value.code == "SMS_EXPIRED"


@pytest.mark.asyncio
async def test_wrong_attempts_survive_caller_rollback_and_lock_challenge(db, provider) -> None:
    sent = await send_sms_challenge(db, "U1", "card", "op-1")
    code = last_code(provider)
    for _ in range(sms.MAX_ATTEMPTS):
        with pytest.raises(BankError) as error:
            await verify_sms_for_operation(db, "U1", other(code), challenge_id=sent["challenge_id"],
                                           purpose="card", reference_id="op-1")
        assert error.value.code == "SMS_INVALID"
        await db.rollback()
    assert (await challenge(db, sent["challenge_id"])).attempts == sms.MAX_ATTEMPTS
    with pytest.raises(BankError) as locked:
        await verify_sms_for_operation(db, "U1", code, challenge_id=sent["challenge_id"],
                                       purpose="card", reference_id="op-1")
    assert locked.value.code == "SMS_LOCKED"


@pytest.mark.asyncio
async def test_resend_cooldown_invalidates_previous_code_and_hourly_cap(db, provider) -> None:
    first = await send_sms_challenge(db, "U1", "card", "op-1")
    first_code = last_code(provider)
    with pytest.raises(BankError) as cooldown:
        await send_sms_challenge(db, "U1", "card", "op-1")
    assert cooldown.value.code == "SMS_COOLDOWN"

    row = await challenge(db, first["challenge_id"])
    row.created_at -= timedelta(seconds=sms.RESEND_COOLDOWN_SECONDS + 1)
    await db.commit()
    await send_sms_challenge(db, "U1", "card", "op-1")
    with pytest.raises(BankError) as superseded:
        await verify_sms_for_operation(db, "U1", first_code, challenge_id=first["challenge_id"],
                                       purpose="card", reference_id="op-1")
    assert superseded.value.code == "SMS_USED"

    for index in range(sms.MAX_SENDS_PER_HOUR - 2):
        await send_sms_challenge(db, "U1", "card", f"op-cap-{index}")
    with pytest.raises(BankError) as capped:
        await send_sms_challenge(db, "U1", "card", "op-cap-last")
    assert capped.value.code == "SMS_RATE_LIMITED"


@pytest.mark.asyncio
async def test_card_red_path_accepts_sms_instead_of_authenticator(client, db, provider) -> None:
    view = (await client.post("/api/v1/cards/operations", headers=headers(),
                              json={"operation_id": "op-lost-1", "action": "card_report_lost",
                                    "card_id": "C1"})).json()
    assert view["requires_mfa"] is True
    await client.post("/api/v1/cards/operations/op-lost-1/confirm", headers=headers(),
                      json={"digest": view["digest"]})

    sent = await client.post("/api/v1/auth/sms/send", headers=headers(),
                             json={"purpose": "card", "reference_id": "op-lost-1"})
    assert sent.status_code == 200, sent.text
    challenge_id = sent.json()["challenge_id"]
    inbox = await client.get("/api/v1/auth/sms/dev-inbox", headers=headers())
    assert inbox.status_code == 200 and "卡片管理" in inbox.json()[0]["content"]
    assert (await client.get("/api/v1/auth/sms/dev-inbox", headers=headers("U2"))).json() == []
    code = last_code(provider)

    wrong = await client.post("/api/v1/cards/operations/op-lost-1/verify", headers=headers(),
                              json={"proof": other(code), "method": "sms", "challenge_id": challenge_id})
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "SMS_INVALID"

    done = await client.post("/api/v1/cards/operations/op-lost-1/verify", headers=headers(),
                             json={"proof": code, "method": "sms", "challenge_id": challenge_id})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"
    audit = (await db.execute(select(AuditLogORM).where(
        AuditLogORM.action == "card_report_lost_verify"))).scalar_one()
    assert audit.mfa_method == "sms" and "sms_verified" in audit.decision_chain


@pytest.mark.asyncio
async def test_send_endpoint_rejects_unknown_purpose_and_extra_fields(client) -> None:
    bad_purpose = await client.post("/api/v1/auth/sms/send", headers=headers(),
                                    json={"purpose": "login", "reference_id": "x"})
    assert bad_purpose.status_code == 422
    smuggled_phone = await client.post("/api/v1/auth/sms/send", headers=headers(),
                                       json={"purpose": "card", "reference_id": "x", "phone": "13900000000"})
    assert smuggled_phone.status_code == 422
    assert (await client.post("/api/v1/auth/sms/send",
                              json={"purpose": "card", "reference_id": "x"})).status_code in (401, 403)


@pytest.mark.asyncio
async def test_sms_can_be_disabled(db, provider, monkeypatch) -> None:
    sent = await send_sms_challenge(db, "U1", "card", "op-1")
    monkeypatch.setenv("MFA_SMS_ENABLED", "false")
    with pytest.raises(BankError) as send_error:
        await send_sms_challenge(db, "U1", "card", "op-2")
    assert send_error.value.code == "SMS_DISABLED"
    with pytest.raises(BankError) as verify_error:
        await verify_sms_for_operation(db, "U1", last_code(provider), challenge_id=sent["challenge_id"],
                                       purpose="card", reference_id="op-1")
    assert verify_error.value.code == "SMS_DISABLED"
