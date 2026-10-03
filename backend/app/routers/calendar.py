"""登录用户自己的日历事件与 .ics 上传。"""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from ..middleware.auth import get_current_user
from ..schemas.calendar import CalendarEventCreate, CalendarEventView, CalendarImportResult
from ..services.calendar import events

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


def failure(error: events.CalendarError):
    return HTTPException(status_code=error.status_code, detail={"message": error.message})


@router.get("/events", response_model=list[CalendarEventView])
async def list_events(
    start: datetime, end: datetime, time_zone: str = Query(default="UTC", max_length=100),
    user: dict = Depends(get_current_user),
):
    try:
        return await events.list_events(user["user_id"], start, end, time_zone)
    except events.CalendarError as error:
        raise failure(error) from error


@router.post("/events", response_model=CalendarEventView, status_code=201)
async def create_event(request: CalendarEventCreate, user: dict = Depends(get_current_user)):
    return await events.create_event(user["user_id"], request)


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: str, user: dict = Depends(get_current_user)):
    try:
        await events.delete_event(user["user_id"], event_id)
        return Response(status_code=204)
    except events.CalendarError as error:
        raise failure(error) from error


@router.post("/import", response_model=CalendarImportResult)
async def import_calendar(
    file: UploadFile = File(...), time_zone: str = Form(default="UTC", max_length=100),
    user: dict = Depends(get_current_user),
):
    try:
        if not (file.filename or "").lower().endswith(".ics"):
            raise events.CalendarError("请上传 .ics 格式的日历文件")
        data = await file.read(events.MAX_FILE_SIZE + 1)
        return await events.import_events(user["user_id"], data, time_zone)
    except events.CalendarError as error:
        raise failure(error) from error
    finally:
        await file.close()
