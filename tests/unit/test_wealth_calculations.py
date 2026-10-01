"""理财精度、交易日历与模拟量表验收。"""

from datetime import datetime, timezone, date
from decimal import Decimal

import pytest

from backend.app.domain.wealth import purchase, redeem, pricing_date, advance_business, units
from backend.app.domain.wealth_risk import score, QUESTIONS


def test_purchase_cash_conservation() -> None:
    result = purchase(1_000_000, "1.23456789", "0.01")
    assert result.fee_fen == 9901 and result.net_fen == 990099
    used = Decimal(result.units) * Decimal("1.23456789")
    assert used + Decimal(result.rounding_yuan) + Decimal(result.refund_fen + result.fee_fen) / 100 == 10000


def test_negative_return_and_final_cost_tail() -> None:
    first = redeem("3.3333", "0.90000000", "0.01", lot_units="10.0000", lot_cost_fen=1001)
    final = redeem("6.6667", "0.90000000", "0.01", lot_units="6.6667", lot_cost_fen=1001-first.cost_fen)
    assert first.cost_fen + final.cost_fen == 1001
    assert first.realized_pnl_fen + final.realized_pnl_fen < 0


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0.00001", "0", "1e1000"])
def test_invalid_units(value: str) -> None:
    with pytest.raises(ValueError):
        units(value)


def test_cutoff_weekend_and_mock_holidays() -> None:
    assert pricing_date(datetime(2026, 9, 30, 6, 59, tzinfo=timezone.utc)) == date(2026, 9, 30)
    assert pricing_date(datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)) == date(2026, 10, 8)
    assert advance_business(date(2026, 9, 25), 1) == date(2026, 9, 28)
    with pytest.raises(ValueError):
        pricing_date(datetime(2026, 9, 30))


def test_assessment_bounds_and_no_loss_policy() -> None:
    assert score({key: 3 for key, _, _ in QUESTIONS}, adult=True, full_capacity=True)["capacity_level"] == 5
    low = {key: 0 for key, _, _ in QUESTIONS}
    assert score(low, adult=True, full_capacity=True)["purchase_allowed"] is False
    low["purpose"] = 3
    with pytest.raises(ValueError, match="矛盾"):
        score(low, adult=True, full_capacity=True)
    with pytest.raises(ValueError, match="成年"):
        score(low, adult=False, full_capacity=True)


@pytest.mark.parametrize("value", [True, 4, -1, 1.5])
def test_assessment_rejects_untrusted_answer_values(value: object) -> None:
    answers = {key: 1 for key, _, _ in QUESTIONS}
    answers["loss"] = value
    with pytest.raises(ValueError):
        score(answers, adult=True, full_capacity=True)
