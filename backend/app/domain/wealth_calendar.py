"""Immutable, complete calendar lookup; unknown coverage never falls back to weekdays."""
import json
from functools import lru_cache
from pathlib import Path
from datetime import date

CALENDAR_VERSION='cn-trading-2021-2027-v1'
PATH=Path(__file__).resolve().parents[3]/'config/wealth/data/calendar.json'


@lru_cache(maxsize=1)
def load():
    data=json.loads(PATH.read_text(encoding='utf-8'))
    if data['calendar_version']!=CALENDAR_VERSION:raise ValueError('CALENDAR_VERSION_MISMATCH')
    return data,{row['date']:row for row in data['days']}


def day_info(day:date)->dict:
    row=load()[1].get(day.isoformat())
    if row is None:raise ValueError('CALENDAR_OUT_OF_COVERAGE')
    return row
