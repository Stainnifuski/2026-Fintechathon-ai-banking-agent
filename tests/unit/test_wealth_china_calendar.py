from datetime import date,datetime
import pytest
from backend.app.domain.wealth_calendar import day_info,CALENDAR_VERSION
from backend.app.domain.wealth import business_day,advance_business,pricing_date


@pytest.mark.parametrize('day',['2022-02-01','2023-10-06','2024-02-09','2025-02-04','2026-02-23','2026-10-07'])
def test_actual_exchange_holidays(day):
    value=day_info(date.fromisoformat(day))
    assert not value['trading'] and value['source_status']=='official_actual'


@pytest.mark.parametrize('day',['2023-01-28','2024-02-18','2025-02-08','2026-02-28','2026-10-10'])
def test_makeup_weekends_are_not_trading_days(day):
    assert not business_day(date.fromisoformat(day),CALENDAR_VERSION)


def test_prediction_coverage_and_t_plus_one():
    assert day_info(date(2027,2,6))['source_status']=='predicted_simulation'
    assert not business_day(date(2027,2,6),CALENDAR_VERSION)
    assert advance_business(date(2026,9,30),1,CALENDAR_VERSION)==date(2026,10,8)
    assert pricing_date(datetime.fromisoformat('2026-09-30T15:01:00+08:00'),calendar_version=CALENDAR_VERSION)==date(2026,10,8)
    with pytest.raises(ValueError,match='OUT_OF_COVERAGE'):business_day(date(2028,1,1),CALENDAR_VERSION)
