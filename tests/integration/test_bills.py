"""Billing totals, evidence and authenticated read-only API regression checks."""
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Base, UserORM, AccountORM, TransactionORM
from backend.app.schemas.bills import BillAnalysis, BillQuery
from backend.app.services.bank.bill_service import analyze, transactions, bounds, TZ
from backend.app.services.bank.transfer_service import BankError
from backend.app.services.ai.bills import interpret, understand, Clarification, Extraction

NOW = datetime(2026, 10, 15, 12, tzinfo=TZ)
QUERY = BillQuery(date="2026-09")


def tx(key, amount, kind="purchase", category="dining", account="A1", when="2026-09-10T04:00:00", **kwargs):
    return TransactionORM(tx_id=key, account_id=account, type=kind, category=category,
                          amount_fen=amount, timestamp=datetime.fromisoformat(when),
                          counterparty=kwargs.pop("counterparty", "商户"), status=kwargs.pop("status", "completed"),
                          channel="app", **kwargs)


@pytest_asyncio.fixture
async def db(monkeypatch):
    from backend.app.services.bank import bill_service
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(bill_service, "datetime", FrozenDatetime)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([UserORM(user_id="U1", name="甲", phone="13800000001", id_card="demo", password_hash="hash", status="active"),
                         UserORM(user_id="U2", name="乙", phone="13800000002", id_card="demo", password_hash="hash", status="active")])
        await session.flush()
        session.add_all([AccountORM(account_id=key, user_id=owner, account_type="savings", card_number=key, balance_fen=100000)
                         for key, owner in [("A1", "U1"), ("A2", "U1"), ("A3", "U2")]])
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_ledger_precision_and_pagination(db):
    db.add_all([tx(f"T{i:04}", 1) for i in range(601)])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    assert result["totals"]["consumption_fen"] == 601
    assert result["totals"]["count"] == 601
    assert sum(p["consumption_fen"] for p in result["trend"]) == 601
    assert sum(c["amount_fen"] for c in result["categories"]) == 601
    page = await transactions(db, "U1", QUERY, page=31, as_of=NOW)
    assert page["total"] == 601 and len(page["items"]) == 1
    assert page["items"][0]["tx_id"] == "T0000"
    BillAnalysis(**result)


@pytest.mark.asyncio
async def test_cashflows_refunds_and_internal_transfers(db):
    db.add_all([tx("salary", 100000, "salary"), tx("purchase", 20000),
                tx("sub", 1500, "subscription", category="other"), tx("refund", 3000, "refund"),
                tx("out", 4000, "transfer_out", counterparty="A3"), tx("in", 2000, "transfer_in", counterparty="A3"),
                tx("internal-out", 5000, "transfer_out", counterparty="A2"),
                tx("internal-in", 5000, "transfer_in", account="A2", counterparty="A1"),
                tx("pending", 9999999, status="pending"), tx("failed", 88888, status="failed"),
                tx("cancelled", 77777, status="cancelled"), tx("other-user", 8888, account="A3")])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    totals = result["totals"]
    assert totals["inflow_fen"] == 105000
    assert totals["outflow_fen"] == 25500
    assert totals["net_consumption_fen"] == 18500
    assert totals["count"] == 6
    assert result["categories"][1]["category"] == "subscription"
    single = await analyze(db, "U1", BillQuery(date="2026-09", account_id="A1"), now=NOW)
    assert single["totals"]["transfer_out_fen"] == 9000
    filtered = await analyze(db, "U1", BillQuery(date="2026-09", category="dining"), now=NOW)
    assert filtered["totals"]["consumption_fen"] == 20000
    assert filtered["totals"]["refund_fen"] == 0
    detail = await transactions(db, "U1", QUERY, as_of=NOW)
    assert detail["total"] == totals["count"]
    assert await db.scalar(select(func.sum(AccountORM.balance_fen))) == 300000


@pytest.mark.asyncio
async def test_unknown_types_not_treated_as_income_and_negative_net_consumption(db):
    db.add_all([tx("unknown", 99999, "mystery"), tx("refund", 1000, "refund")])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    assert result["totals"]["inflow_fen"] == 1000
    assert result["totals"]["net_consumption_fen"] == -1000
    assert result["totals"]["unknown_count"] == 1


@pytest.mark.asyncio
async def test_beijing_boundaries_year_and_empty_period(db):
    db.add_all([tx("before", 100, when="2026-08-31T15:59:59"),
                tx("start", 200, when="2026-08-31T16:00:00"),
                tx("last", 300, when="2026-09-30T15:59:59"),
                tx("next", 400, when="2026-09-30T16:00:00"),
                tx("year", 500, when="2025-12-31T16:00:00")])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    assert result["totals"]["consumption_fen"] == 500
    assert result["trend"][0]["consumption_fen"] == 200
    assert result["trend"][-1]["consumption_fen"] == 300
    year = await analyze(db, "U1", BillQuery(period="year", date="2026"), now=NOW)
    assert year["totals"]["consumption_fen"] == 1500
    assert year["trend"][0]["consumption_fen"] == 500
    assert len(year["trend"]) == 10
    empty = await analyze(db, "U1", BillQuery(date="2024-02"), now=NOW)
    assert len(empty["trend"]) == 29 and empty["totals"]["count"] == 0
    assert empty["change_percent"] is None and empty["anomalies"] == []


def test_comparison_clamps_month_and_leap_year():
    b = bounds(BillQuery(date="2026-03"), datetime(2026, 3, 31, 12, tzinfo=TZ))
    assert b["previous_end_exclusive"] == datetime(2026, 2, 28, 12, tzinfo=TZ)
    b = bounds(BillQuery(period="year", date="2024"), datetime(2024, 2, 29, 9, tzinfo=TZ))
    assert b["previous_end_exclusive"] == datetime(2023, 2, 28, 9, tzinfo=TZ)


@pytest.mark.parametrize("date", ["2026-13", "2026-1", "1999-01", "2099-01", "2026-09-01"])
def test_invalid_period(date):
    with pytest.raises((BankError, ValueError)):
        bounds(BillQuery(date=date), NOW)


@pytest.mark.asyncio
async def test_anomaly_evidence_thresholds_and_scoping(db):
    db.add_all([tx(f"baseline-{i}", 1000, when=f"2026-08-{i+1:02}T04:00:00") for i in range(5)])
    db.add_all([tx("large", 150000, when="2026-09-01T04:00:00"),
                tx("relative", 10000, when="2026-09-02T04:00:00"),
                tx("repeat1", 1500, "subscription", when="2026-09-03T04:00:00"),
                tx("repeat2", 1500, "subscription", when="2026-09-03T04:05:00"),
                tx("not-repeat", 1500, "subscription", when="2026-09-03T04:10:01"),
                tx("other-account", 1500, "subscription", account="A2", when="2026-09-03T04:05:00")])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    kinds = [a["kind"] for a in result["anomalies"]]
    assert kinds.count("large") == 2 and kinds.count("duplicate") == 1 and kinds.count("increase") == 1
    duplicate = next(a for a in result["anomalies"] if a["kind"] == "duplicate")
    assert duplicate["tx_ids"] == ["repeat1", "repeat2"]
    evidence = await transactions(db, "U1", QUERY, tx_ids=duplicate["tx_ids"], as_of=NOW)
    assert evidence["total"] == 2
    assert (await analyze(db, "U2", QUERY, now=NOW))["anomalies"] == []
    with pytest.raises(BankError) as error:
        await analyze(db, "U2", BillQuery(date="2026-09", account_id="A1"), now=NOW)
    assert error.value.status_code == 404


@pytest.mark.parametrize("message,period,date,category", [
    ("上个月餐饮花了多少钱", "month", "2026-09", "dining"),
    ("这个月为什么比上个月花得多", "month", "2026-10", None),
    ("生成今年的年度账单", "year", "2026", None),
    ("去年9月消费", "month", "2025-09", None),
    ("2025年年度账单", "year", "2025", None),
    ("2026-08交通支出", "month", "2026-08", "transport"),
    ("有没有疑似重复扣费", "month", "2026-09", None),
])
def test_natural_language(message, period, date, category):
    parsed = interpret(message, QUERY, now=NOW)
    assert (parsed.period, parsed.date, parsed.category) == (period, date, category)


@pytest.mark.parametrize("message", ["帮我转给李四100元", "取消订阅", "最近七天的账单", "2026年8月和9月账单", "餐饮和购物各多少", "尾号1234银行卡账单", "天气怎么样"])
def test_ambiguous_or_write_requests_need_clarification(message):
    with pytest.raises(Clarification):
        interpret(message, QUERY, now=NOW)


@pytest.mark.asyncio
async def test_model_grounding_failure_fallback(monkeypatch):
    from backend.app.services.ai import bills
    monkeypatch.setenv("BILL_NLU_MODE", "model")
    async def malicious(_):
        return Extraction(intent="bill", time_text="2025年", account_text="A3")
    monkeypatch.setattr(bills, "model_extract", malicious)
    query, mode = await understand("查看餐饮消费", QUERY)
    assert mode == "fallback" and query.category == "dining" and query.account_id is None
    async def valid(_):
        return Extraction(intent="bill", category_text="餐饮")
    monkeypatch.setattr(bills, "model_extract", valid)
    query, mode = await understand("查看餐饮消费", QUERY)
    assert mode == "model" and query.category == "dining"
    async def unavailable(_):
        raise TimeoutError()
    monkeypatch.setattr(bills, "model_extract", unavailable)
    query, mode = await understand("查看餐饮消费", QUERY)
    assert mode == "fallback" and query.category == "dining"


@pytest.mark.asyncio
async def test_partial_month_compares_same_cutoff_and_details(db):
    now = datetime(2026, 9, 15, 12, tzinfo=TZ)
    db.add_all([tx("current", 6000, when="2026-09-15T03:59:59"),
                tx("current-later", 99999, when="2026-09-15T04:00:00"),
                tx("previous", 4000, when="2026-08-15T03:59:59"),
                tx("previous-later", 99999, when="2026-08-15T04:00:00")])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=now)
    assert result["partial"] is True
    assert result["totals"]["consumption_fen"] == 6000
    assert result["previous_totals"]["consumption_fen"] == 4000
    assert result["change_percent"] == 50
    assert (await transactions(db, "U1", QUERY, as_of=now))["total"] == 1


@pytest.mark.asyncio
async def test_growth_rule_uses_exact_ratio_not_rounded_display(db):
    db.add_all([tx("previous", 100001, when="2026-08-10T04:00:00"), tx("current", 150001)])
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    assert result["change_percent"] == 50.0  # rounded display is allowed
    assert not any(a["kind"] == "increase" for a in result["anomalies"])


@pytest.mark.asyncio
async def test_demo_expected_totals(db):
    from backend.app.services.bank.bill_demo import demo_transactions
    rows = demo_transactions()
    mapping = {"A001": "A1", "A002": "A2", "A003": "A3"}
    for row in rows:
        row.account_id = mapping[row.account_id]
        row.counterparty = mapping.get(row.counterparty, row.counterparty)
    db.add_all(rows)
    await db.commit()
    result = await analyze(db, "U1", QUERY, now=NOW)
    assert result["totals"]["consumption_fen"] == 259500
    assert result["totals"]["net_consumption_fen"] == 254500
    assert result["totals"]["inflow_fen"] == 1505000
    assert result["totals"]["outflow_fen"] == 269500
    assert result["totals"]["count"] == 19
    assert len(result["anomalies"]) == 5


@pytest.mark.asyncio
async def test_api_auth_pagination_validation_and_read_only(db, monkeypatch):
    from backend.app.main import app
    from backend.app.database.connection import get_db
    from backend.app.middleware.auth import create_access_token
    monkeypatch.setenv("BILL_NLU_MODE", "offline")
    db.add_all([tx("mine", 1234), tx("foreign", 88888, account="A3")])
    await db.commit()
    async def override():
        yield db
    app.dependency_overrides[get_db] = override
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "U1"})}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/bills/analysis")).status_code == 401
            response = await client.get("/api/v1/bills/analysis?date=2026-09", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["totals"]["consumption_fen"] == 1234
            for path in ["analysis", "transactions"]:
                response = await client.get(f"/api/v1/bills/{path}?date=2026-09&account_id=A3", headers=headers)
                assert response.status_code == 404
            for query in ["page=0", "page_size=101", "category=invalid", "date=bad", "as_of=2026-01-01T00:00:00"]:
                assert (await client.get("/api/v1/bills/transactions?" + query, headers=headers)).status_code == 422
            response = await client.get("/api/v1/bills/transactions?date=2026-09&tx_ids=foreign", headers=headers)
            assert response.json()["total"] == 0
            response = await client.post("/api/v1/bills/messages", headers=headers, json={"message": "2026年9月餐饮花了多少"})
            assert response.status_code == 200, response.text
            assert response.json()["analysis"]["totals"]["consumption_fen"] == 1234
            response = await client.post("/api/v1/bills/messages", headers=headers, json={"message": "给李四转100元"})
            assert response.json()["kind"] == "clarification"
            assert await db.scalar(select(func.count()).select_from(TransactionORM)) == 2
            assert await db.scalar(select(func.sum(AccountORM.balance_fen))) == 300000
    finally:
        app.dependency_overrides.pop(get_db, None)
