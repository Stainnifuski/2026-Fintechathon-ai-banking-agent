from datetime import datetime,date,timedelta
from decimal import Decimal
import math
import statistics
import pytest
from backend.app.domain.wealth_chart import chart,window_start


def series(values):
    start=date(2026,8,3);rows=[];index=0
    while len(rows)<len(values):
        day=start+timedelta(days=index);index+=1
        if day.weekday()>=5:continue
        rows.append({'date':day.isoformat(),'nav':str(values[len(rows)]),'available_at':day.isoformat()+'T10:00:00+00:00'})
    return rows


def calculate(rows,as_of='2026-09-30T10:00:00+00:00'):
    return chart(rows,as_of=datetime.fromisoformat(as_of),period='1y',calendar_version='synthetic-weekdays-v1',data_version='fixture',simulated=True)


def test_hand_calculated_drawdown_and_nav_change():
    result=calculate(series(['1','1.2','0.9','1.08']))
    assert result['metrics']['nav_change']['value_percent']=='8.00'
    assert result['metrics']['max_drawdown']['value_percent']=='25.00'
    assert result['metrics']['volatility']['reason']=='AT_LEAST_TWENTY_RETURNS'


def test_sample_volatility_independent_reference():
    values=[Decimal('1')]
    rates=[Decimal('0.01') if i%2==0 else Decimal('-0.01') for i in range(20)]
    for rate in rates:values.append(values[-1]*(1+rate))
    expected=statistics.stdev([float(r) for r in rates])*math.sqrt(252)*100
    result=calculate(series(values))
    assert float(result['metrics']['volatility']['value_percent'])==pytest.approx(expected,abs=.005)
    assert result['return_count']==20


def test_future_publication_carry_and_quality():
    rows=series(['1','1.1','1.2'])
    visible=calculate(rows,'2026-08-04T09:59:00+00:00')
    assert visible['observation_count']==1
    carry={'date':'2026-08-08','nav':'1.2','available_at':'2026-08-08T10:00:00+00:00','original_nav_date':'2026-08-05'}
    assert calculate(rows+[carry])['return_count']==2
    rows=series([Decimal('1')+Decimal(i)/1000 for i in range(24)]);rows.pop(5)
    result=calculate(rows)
    assert 'MISSING_EXPECTED_VALUATION' in result['quality_flags']
    assert result['metrics']['volatility']['reason']=='DATA_QUALITY_REVIEW_REQUIRED'


def test_empty_one_point_and_calendar_window():
    assert calculate([])['metrics']['nav_change']['value_percent'] is None
    assert calculate(series(['1']))['metrics']['max_drawdown']['value_percent'] is None
    assert window_start(date(2026,3,31),'1m')==date(2026,2,28)
    assert window_start(date(2024,2,29),'1y')==date(2023,2,28)
