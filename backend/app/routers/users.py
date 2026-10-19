"""转账收款人查询接口。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..services.bank import list_recipients as bank_list_recipients
from ..services.bank import lookup_recipient_phone, BankError
from ..services.bank.data_service import lookup_recipient_card
from ..services.bank import contacts as contact_service
from ..schemas.bank import RecipientView, PhoneRecipientView, BANK_ERROR_RESPONSES

router = APIRouter(prefix="/api/v1/users", tags=["users"], responses=BANK_ERROR_RESPONSES)


@router.get("/recipients", response_model=list[RecipientView])
async def list_recipients(
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """返回其它活跃用户及其首张可收款账户，供转账表单选择。"""
    return await bank_list_recipients(current_user["user_id"])



class PhoneLookupRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=32)


class CardLookupRequest(BaseModel):
    card_number: str = Field(min_length=1, max_length=32)


class SavedNameLookupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TransferRecipientResolveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    mode: str = Field(pattern="^(card|phone)$")
    number: str | None = Field(default=None, max_length=32)
    account_id: str | None = None


class ContactCreateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    phone: str | None = None
    card_number: str | None = None
    account_id: str | None = None
    nickname: str | None = None


class ContactRenameRequest(BaseModel):
    nickname: str = ""


def bank_error(error: BankError) -> HTTPException:
    return HTTPException(status_code=error.status_code,
                         detail={"code": error.code, "message": error.message})


@router.post("/recipients/phone", response_model=list[PhoneRecipientView])
async def lookup_phone(
    request: PhoneLookupRequest,
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """仅按完整手机号精确查询，账户候选脱敏返回。"""
    try:
        return await lookup_recipient_phone(current_user["user_id"], request.phone)
    except BankError as error:
        raise bank_error(error) from error


@router.post("/recipients/card", response_model=list[PhoneRecipientView])
async def lookup_card(request: CardLookupRequest, current_user: dict = Depends(get_current_user)):
    try:
        return await lookup_recipient_card(current_user["user_id"], request.card_number)
    except BankError as error:
        raise bank_error(error) from error


@router.post("/recipients/saved-name", response_model=list[PhoneRecipientView])
async def lookup_saved_name(request: SavedNameLookupRequest, current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    from ..services.bank.transfer_service import transfer_directory
    try:
        result = await transfer_directory(db, subject_user_id=current_user["user_id"],
                                          recipient_name=request.name, contacts_only=True)
        return [{"account_id": row["account_id"], "label": row["label"],
                 "name": row["recipient_name"]} for row in result["recipients"]]
    except BankError as error:
        raise bank_error(error) from error


@router.post("/recipients/resolve", response_model=PhoneRecipientView)
async def resolve_transfer_recipient(request: TransferRecipientResolveRequest,
                                     current_user: dict = Depends(get_current_user)):
    try:
        return await contact_service.resolve_transfer_recipient(
            current_user["user_id"], request.name, request.mode, request.number,
            request.account_id)
    except BankError as error:
        raise bank_error(error) from error


@router.get("/contacts")
async def list_contacts(current_user: dict = Depends(get_current_user)):
    return await contact_service.list_contacts(current_user["user_id"])


@router.post("/contacts", status_code=201)
async def create_contact(request: ContactCreateRequest, current_user: dict = Depends(get_current_user)):
    try:
        return await contact_service.create_contact(current_user["user_id"], request.phone,
                                                    request.account_id, request.nickname, request.card_number,
                                                    request.name)
    except BankError as error:
        raise bank_error(error) from error


@router.patch("/contacts/{contact_id}")
async def rename_contact(contact_id: str, request: ContactRenameRequest,
                         current_user: dict = Depends(get_current_user)):
    try:
        return await contact_service.rename_contact(current_user["user_id"], contact_id, request.nickname)
    except BankError as error:
        raise bank_error(error) from error


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(contact_id: str, current_user: dict = Depends(get_current_user)):
    try:
        await contact_service.delete_contact(current_user["user_id"], contact_id)
    except BankError as error:
        raise bank_error(error) from error


@router.post("/contacts/{contact_id}/resolve", response_model=list[PhoneRecipientView])
async def resolve_contact(contact_id: str, current_user: dict = Depends(get_current_user)):
    try:
        return await contact_service.resolve_contact(current_user["user_id"], contact_id)
    except BankError as error:
        raise bank_error(error) from error


@router.get("/transfer-recipients/history")
async def transfer_history(current_user: dict = Depends(get_current_user)):
    return await contact_service.list_transfer_history(current_user["user_id"])


@router.delete("/transfer-recipients/history", status_code=204)
async def clear_transfer_history(current_user: dict = Depends(get_current_user)):
    await contact_service.clear_transfer_history(current_user["user_id"])
