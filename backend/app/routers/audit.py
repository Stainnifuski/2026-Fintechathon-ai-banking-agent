"""Read-only access to the authenticated owner's audit evidence."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..models.audit_event import AuditEventORM
from ..services.audit.query import query_events, object_timeline, export_events, AuditQueryError

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def filters(since: datetime | None = None, until: datetime | None = None,
            domain: str | None = Query(None, max_length=32), action: str | None = Query(None, max_length=128),
            outcome: str | None = Query(None, max_length=32), permission_level: str | None = Query(None, max_length=16),
            object_type: str | None = Query(None, max_length=40), object_id: str | None = Query(None, max_length=128),
            view: Literal["all", "business"] = "all"):
    return dict(since=since, until=until, domain=domain, action=action, outcome=outcome, permission_level=permission_level,
                object_type=object_type, object_id=object_id, view=view)


def fail(error):
    raise HTTPException(422, detail={"code": "AUDIT_QUERY_INVALID", "message": str(error)}) from None


@router.get("/events")
async def events(params: dict = Depends(filters), cursor: str | None = Query(None, max_length=4096),
                 limit: int = Query(50, ge=1, le=200), user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await query_events(db, user["user_id"], cursor=cursor, limit=limit, **params)
    except AuditQueryError as error:
        fail(error)


@router.get("/traces/{trace_id}")
async def trace(trace_id: str, cursor: str | None = Query(None, max_length=4096),
                user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        page = await query_events(db, user["user_id"], trace_id=trace_id, cursor=cursor, limit=200)
        if not page["items"]:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "未找到操作记录"})
        page["items"].reverse()
        return page
    except AuditQueryError as error:
        fail(error)


@router.get("/objects/{object_type}/{object_id}")
async def object_events(object_type: str, object_id: str, domain: str | None = Query(None, max_length=32),
                        user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await object_timeline(db, user["user_id"], object_type, object_id, domain)
    if not data["items"]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "未找到操作记录"})
    return data


@router.get("/export")
async def export(since: datetime, until: datetime, domain: str | None = Query(None, max_length=32),
                 outcome: str | None = Query(None, max_length=32), action: str | None = Query(None, max_length=128),
                 permission_level: str | None = Query(None, max_length=16),
                 object_type: str | None = Query(None, max_length=40), object_id: str | None = Query(None, max_length=128),
                 view: Literal["all", "business"] = "all",
                 user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        result = await export_events(db, user["user_id"], since=since, until=until, domain=domain,
                                     outcome=outcome, action=action, permission_level=permission_level,
                                     object_type=object_type, object_id=object_id, view=view)
        from ..services.audit.writer import emit
        emit(action="audit.export", phase="result", outcome="request_completed", domain="audit",
             source_module="audit.api", facts={"export_count": len(result["items"]), "since": since, "until": until})
        return JSONResponse(jsonable_encoder(result), headers={
            "Content-Disposition": 'attachment; filename="operation-audit.json"', "Cache-Control": "no-store"})
    except AuditQueryError as error:
        fail(error)
