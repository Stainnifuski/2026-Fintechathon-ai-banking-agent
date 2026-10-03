"""只读现实时间，不暴露账户且不接受模拟推进。"""
from fastapi import APIRouter
from ..domain.time import BUSINESS_TIMEZONE, utc_iso, utc_now

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/time")
async def reality_time() -> dict:
    """供展示校时；授权与账务继续独立使用服务端现实时间。"""
    return {"server_time_utc": utc_iso(utc_now()), "business_timezone": BUSINESS_TIMEZONE}
