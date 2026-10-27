"""Owned payment request lookup and chat review cards; execution stays in bank APIs."""
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from ...schemas.nova import StrictModel
from ..bank.payment_request_service import list_payment_requests
from .card_subscription_tools import _owned_session


class ManagePaymentRequestsArguments(StrictModel):
    action: Literal['list', 'cancel', 'respond'] = Field(description='list 查看；cancel 预览本人发出的待取消申请；respond 预览别人向本人发起的待支付或拒绝申请')
    name: str | None = Field(default=None, max_length=100, description='对方姓名：cancel 时是付款人，respond 时是申请人')
    amount_fen: int | None = Field(default=None, ge=1, description='申请金额，单位为分；用于精确筛选')
    created_from: datetime | None = Field(default=None, description='创建时间下界，含边界，须有时区')
    created_to: datetime | None = Field(default=None, description='创建时间上界，含边界，须有时区')
    payment_request_ids: list[str] | None = Field(default=None, max_length=50, description='已从可信列表查到的申请 ID，可精确指定一笔或多笔')
    role: Literal['all', 'incoming', 'outgoing'] = Field(default='all', description='list 的查询方向；操作时由 action 决定')

    @field_validator('created_from', 'created_to')
    @classmethod
    def aware_time(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('创建时间必须包含时区')
        return value

    @model_validator(mode='after')
    def valid_range(self):
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError('开始时间不能晚于结束时间')
        return self


def _select(items: list[dict], arguments: ManagePaymentRequestsArguments) -> list[dict]:
    ids = set(arguments.payment_request_ids or [])
    selected = []
    for item in items:
        if arguments.action == 'cancel' and item['status'] != 'PENDING':
            continue
        if arguments.action == 'respond' and item['status'] not in {'PENDING', 'ACCEPTED'}:
            continue
        names = ([item['payer_name']] if arguments.action == 'cancel' or
                 (arguments.action == 'list' and arguments.role == 'outgoing') else
                 [item['requester_name']] if arguments.action == 'respond' or
                 (arguments.action == 'list' and arguments.role == 'incoming') else
                 [item['payer_name'], item['requester_name']])
        if arguments.name and not any(name.strip().casefold() == arguments.name.strip().casefold() for name in names):
            continue
        if arguments.amount_fen is not None and item['amount_fen'] != arguments.amount_fen:
            continue
        created = datetime.fromisoformat(item['created_at']) if isinstance(item['created_at'], str) else item['created_at']
        created = created.astimezone(timezone.utc)
        if arguments.created_from and created < arguments.created_from.astimezone(timezone.utc):
            continue
        if arguments.created_to and created > arguments.created_to.astimezone(timezone.utc):
            continue
        if ids and item['payment_request_id'] not in ids:
            continue
        selected.append(item)
    return selected


async def manage_payment_requests(arguments: ManagePaymentRequestsArguments, context) -> dict:
    role = arguments.role if arguments.action == 'list' else 'outgoing' if arguments.action == 'cancel' else 'incoming'
    items = await list_payment_requests(user_id=context.user_id, role=role)
    selected = _select(items, arguments)
    if arguments.action == 'list':
        return {'source': 'bank.payment_requests', 'role': role, 'total': len(selected), 'items': selected}
    if not selected:
        return {'source': 'bank.payment_requests', 'action': arguments.action, 'matched_count': 0,
                'message': '没有找到符合条件且可处理的收款申请；请核对姓名、金额、时间或申请状态。'}
    row = await _owned_session(context.db, context.user_id, context.session_id, create=True)
    state = json.loads(json.dumps(row.state or {}))
    entry = {'entry_id': uuid4().hex, 'origin_request_id': context.request_id,
             'action': arguments.action, 'payment_request_ids': [item['payment_request_id'] for item in selected]}
    state['payment_request_tasks'] = [*(state.get('payment_request_tasks') or []), entry][-50:]
    row.state = state
    await context.db.commit()
    return {'source': 'bank.payment_requests', 'action': arguments.action,
            'entry_id': entry['entry_id'], 'matched_count': len(selected), 'items': selected,
            'message': '已在聊天中打开申请任务卡；取消、支付或拒绝都需要本人在卡片上点击。'}


async def chat_payment_request_tasks(user_id: str, state: dict) -> list[dict]:
    entries = state.get('payment_request_tasks') or []
    if not entries:
        return []
    items = await list_payment_requests(user_id=user_id)
    by_id = {item['payment_request_id']: item for item in items}
    views = []
    for entry in entries:
        requests = [by_id[id] for id in entry['payment_request_ids'] if id in by_id and
                    (by_id[id]['requester_user_id'] == user_id if entry['action'] == 'cancel' else
                     by_id[id]['payer_user_id'] == user_id)]
        views.append({'entry_id': entry['entry_id'], 'origin_request_id': entry['origin_request_id'],
                      'action': entry['action'], 'requests': requests})
    return views
