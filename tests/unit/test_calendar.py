"""日历 HTTP 接口、权限、时间边界与 ICS 导入。"""
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.middleware.auth import get_current_user
from backend.app.models import Base, CalendarEventORM, UserORM
from backend.app.routers.calendar import router
from backend.app.services.calendar import events


def ics(*components):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Tests//Calendar//EN\r\n" + "\r\n".join(components) + "\r\nEND:VCALENDAR\r\n").encode()


def vevent(uid, start, extra="", title="测试事件"):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:{title}\r\n{start}\r\n{extra}\r\nEND:VEVENT"


@pytest_asyncio.fixture
async def store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(events, "AsyncSessionLocal", sessions)
    async with sessions() as session:
        session.add_all([UserORM(user_id=uid, name=uid, phone=phone, id_card="demo", password_hash="hash", status="active")
                         for uid, phone in [("U1", "13800000001"), ("U2", "13800000002")]])
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest_asyncio.fixture
async def client(store):
    app = FastAPI()
    app.include_router(router)
    user = {"user_id": "U1"}
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, user


async def month(client, start="2026-10-01T00:00:00Z", end="2026-11-01T00:00:00Z", zone="UTC"):
    return await client.get("/api/v1/calendar/events", params={"start": start, "end": end, "time_zone": zone})


async def upload(client, data, zone="Asia/Shanghai", filename="events.ics"):
    return await client.post("/api/v1/calendar/import", files={"file": (filename, data, "text/calendar")}, data={"time_zone": zone})


@pytest.mark.asyncio
async def test_manual_point_range_overlap_and_ownership(client):
    c, user = client
    point = await c.post("/api/v1/calendar/events", json={"title": " 取蛋糕 ", "starts_at": "2026-10-02T09:30:00+08:00"})
    assert point.status_code == 201
    assert point.json()["title"] == "取蛋糕"
    assert point.json()["ends_at"] is None
    assert point.json()["starts_at"] == "2026-10-02T01:30:00Z"
    span = await c.post("/api/v1/calendar/events", json={"title": "跨日旅行", "starts_at": "2026-09-30T23:00:00Z", "ends_at": "2026-10-02T00:00:00Z"})
    assert span.status_code == 201
    listed = await month(c)
    assert len(listed.json()) == 2
    # A range ending exactly at the query boundary does not overlap.
    assert len((await month(c, start="2026-10-02T00:00:00Z")).json()) == 1
    user["user_id"] = "U2"
    assert (await month(c)).json() == []
    assert (await c.delete(f"/api/v1/calendar/events/{point.json()['event_id']}")).status_code == 404
    user["user_id"] = "U1"
    assert (await c.delete(f"/api/v1/calendar/events/{point.json()['event_id']}")).status_code == 204
    assert len((await month(c)).json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"title": "   "}, {"starts_at": "2026-10-02T09:00:00"},
    {"ends_at": "2026-10-02T08:00:00Z"}, {"ends_at": "2026-10-02T09:00:00Z"},
])
async def test_invalid_manual_events(client, patch):
    c, _ = client
    result = await c.post("/api/v1/calendar/events", json={"title": "会议", "starts_at": "2026-10-02T09:00:00Z", **patch})
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_ics_timezones_duration_folding_all_day_and_reimport(client):
    c, _ = client
    data = ics(
        vevent("utc", "DTSTART:20261002T090000Z", "DESCRIPTION:一行\\n二行\\,内容\r\nLOCATION:会议\r\n 室"),
        vevent("floating", "DTSTART:20261003T090000", "DURATION:PT1H"),
        vevent("zone", "DTSTART;TZID=America/New_York:20261004T090000", "DTEND;TZID=America/New_York:20261004T100000"),
        vevent("all-day", "DTSTART;VALUE=DATE:20261005", "DTEND;VALUE=DATE:20261007"),
    )
    result = await upload(c, data)
    assert result.status_code == 200, result.text
    assert result.json() == {"created": 4, "updated": 0, "skipped": 0, "total": 4}
    listed = (await month(c)).json()
    assert len(listed) == 4
    assert listed[0]["starts_at"] == "2026-10-02T09:00:00Z"
    assert listed[0]["ends_at"] is None
    assert listed[0]["description"] == "一行\n二行,内容"
    assert listed[0]["location"] == "会议室"
    assert listed[1]["starts_at"] == "2026-10-03T01:00:00Z"
    assert listed[1]["ends_at"] == "2026-10-03T02:00:00Z"
    assert listed[2]["starts_at"] == "2026-10-04T13:00:00Z"
    assert listed[3]["all_day"] is True
    assert listed[3]["ends_at"] == "2026-10-07T00:00:00Z"
    assert (await upload(c, data)).json()["skipped"] == 4
    updated = await upload(c, ics(vevent("utc", "DTSTART:20261002T100000Z", title="已修改")))
    assert updated.json()["updated"] == 1
    assert len((await month(c)).json()) == 4
    assert (await month(c)).json()[0]["title"] == "已修改"


@pytest.mark.asyncio
async def test_recurring_exdates_overrides_future_month_and_delete_series(client):
    c, _ = client
    data = ics(
        vevent("weekly", "DTSTART:20261001T090000Z", "DTEND:20261001T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=10\r\nEXDATE:20261008T090000Z", "例会"),
        vevent("weekly", "DTSTART:20261015T110000Z", "DTEND:20261015T120000Z\r\nRECURRENCE-ID:20261015T090000Z", "改期例会"),
    )
    response = await upload(c, data)
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    october = (await month(c)).json()
    assert len(october) == 4
    assert all(item["recurring"] for item in october)
    assert [item["starts_at"] for item in october] == ["2026-10-01T09:00:00Z", "2026-10-15T11:00:00Z", "2026-10-22T09:00:00Z", "2026-10-29T09:00:00Z"]
    november = (await month(c, "2026-11-01T00:00:00Z", "2026-12-01T00:00:00Z")).json()
    assert len(november) == 4
    assert (await c.delete(f"/api/v1/calendar/events/{october[0]['event_id']}")).status_code == 204
    assert (await month(c)).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("data,filename", [
    (b"not a calendar", "bad.ics"),
    (ics(vevent("missing", "")), "bad.ics"),
    (ics(vevent("bad-range", "DTSTART:20261001T090000Z", "DTEND:20261001T080000Z")), "bad.ics"),
    (ics(vevent("frequent", "DTSTART:20261001T090000Z", "RRULE:FREQ=SECONDLY")), "bad.ics"),
    (ics(vevent("good", "DTSTART:20261001T090000Z")), "bad.txt"),
])
async def test_invalid_imports_do_not_write(client, data, filename):
    c, _ = client
    response = await upload(c, data, filename=filename)
    assert response.status_code == 422, response.text
    assert (await month(c)).json() == []


@pytest.mark.asyncio
async def test_size_limit_bad_timezone_and_atomic_import(client):
    c, _ = client
    assert (await upload(c, b"x" * (events.MAX_FILE_SIZE + 1))).status_code == 413
    assert (await upload(c, b"")).status_code == 413
    data = ics(vevent("a", "DTSTART:20261001T090000Z"))
    assert (await upload(c, data, zone="Unknown/Zone")).status_code == 422
    malformed = ics(vevent("a", "DTSTART:20261001T090000Z"), vevent("b", "DTSTART:invalid"))
    assert (await upload(c, malformed)).status_code == 422
    assert (await month(c)).json() == []
    assert (await month(c, end="2027-11-01T00:00:00Z")).status_code == 422


@pytest.mark.asyncio
async def test_file_timezone_and_user_import_isolation(client):
    c, user = client
    data = ics(vevent("floating", "DTSTART:20261003T090000")).replace(b"VERSION:2.0", b"VERSION:2.0\r\nX-WR-TIMEZONE:America/New_York")
    assert (await upload(c, data, zone="Asia/Shanghai")).status_code == 200
    listed = (await month(c)).json()
    assert listed[0]["starts_at"] == "2026-10-03T13:00:00Z"
    user["user_id"] = "U2"
    assert (await month(c)).json() == []
    assert (await upload(c, data)).json()["created"] == 1
    user["user_id"] = "U1"
    assert len((await month(c)).json()) == 1


@pytest.mark.asyncio
async def test_all_day_boundaries_and_dst_recurrence(client):
    c, _ = client
    data = ics(
        vevent("day", "DTSTART;VALUE=DATE:20261001"),
        vevent("dst", "DTSTART;TZID=America/New_York:20261025T090000", "DTEND;TZID=America/New_York:20261025T100000\r\nRRULE:FREQ=WEEKLY;COUNT=3"),
    )
    response = await upload(c, data)
    assert response.status_code == 200, response.text
    listed = (await month(c, "2026-10-01T04:00:00Z", "2026-11-01T04:00:00Z", "America/New_York")).json()
    assert any(item["all_day"] and item["starts_at"].startswith("2026-10-01") for item in listed)
    november = (await month(c, "2026-11-01T04:00:00Z", "2026-12-01T05:00:00Z", "America/New_York")).json()
    assert [item["starts_at"] for item in november] == ["2026-11-01T14:00:00Z", "2026-11-08T14:00:00Z"]
