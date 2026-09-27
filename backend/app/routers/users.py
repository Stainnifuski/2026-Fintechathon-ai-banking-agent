"""转账收款人查询接口。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..middleware.auth import get_current_user
from ..services.bank import list_recipients as bank_list_recipients
from ..services.bank import lookup_recipient_phone, BankError

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/recipients")
async def list_recipients(
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """返回其它活跃用户及其首张可收款账户，供转账表单选择。"""
    return await bank_list_recipients(current_user["user_id"])



class PhoneLookupRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=32)


@router.post("/recipients/phone")
async def lookup_phone(
    request: PhoneLookupRequest,
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """仅按完整手机号精确查询，账户候选脱敏返回。"""
    try:
        return await lookup_recipient_phone(current_user["user_id"], request.phone)
    except BankError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
