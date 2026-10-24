"""卡片与订阅工具；身份只由服务端提供。卡片操作只生成待本人核对的预览，不生成确认或验证凭证。"""
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator

from ...domain.time import as_utc, utc_iso, utc_now
from ...integrations.card_authorization import CardAuthorizationError
from ...schemas.cards import ACTION_FIELDS, CHANNEL_FIELDS, CardAction, CardOperationCreate, CardView
from ...schemas.subscriptions import SubscriptionView
from ...schemas.nova import StrictModel
from ...models.card import CardOperationORM
from ...models.nova_task import NovaSessionORM
from ..bank import card_service, subscription_service
from ..bank.transfer_service import BankError
from ..card_operations import ACTION_LABELS, open_preview, operation_view

MAX_CHAT_CARD_OPERATIONS = 30


class ListArguments(StrictModel):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)


class CardListArguments(ListArguments):
    status: Literal['ACTIVE', 'LOCKED', 'LOST', 'CANCELLED'] | None = None
    card_type: Literal['DEBIT', 'CREDIT'] | None = None
    is_virtual: bool | None = None
    last_four: str | None = Field(default=None, pattern=r'^\d{4}$')


class CardArguments(StrictModel):
    card_id: str | None = Field(default=None, min_length=1, max_length=32)


class CardTransactionArguments(CardArguments):
    approved: bool | None = Field(default=None, description='true 仅看已批准，false 仅看被拒绝；省略为全部')


YUAN_PATTERN = r'^\d{1,9}(\.\d{1,2})?$'


class CardPreviewArguments(StrictModel):
    action: CardAction
    card_id: str | None = Field(default=None, min_length=1, max_length=32)
    account_id: str | None = Field(default=None, min_length=1, max_length=32)
    credit_limit: str | None = Field(default=None, pattern=YUAN_PATTERN)
    single_txn_limit: str | None = Field(default=None, pattern=YUAN_PATTERN)
    daily_txn_limit: str | None = Field(default=None, pattern=YUAN_PATTERN)
    online_enabled: bool | None = None
    atm_enabled: bool | None = None
    overseas_enabled: bool | None = None
    contactless_enabled: bool | None = None
    value_quotes: dict[Literal['credit_limit','single_txn_limit','daily_txn_limit'],str] = Field(default_factory=dict,max_length=3)


class SubscriptionListArguments(ListArguments):
    status: Literal['ACTIVE', 'CANCELLED'] | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator('keyword')
    @classmethod
    def valid_keyword(cls, value):
        if value is not None and not value.strip():
            raise ValueError('商户关键词不能为空')
        return value.strip() if value else value


class SubscriptionArguments(StrictModel):
    subscription_id: str | None = Field(default=None, min_length=1, max_length=40)


class SubscriptionCancelArguments(SubscriptionArguments):
    pass


def masked(value):
    return '****' + re.sub(r'\D', '', value)[-4:]


def envelope(source, data):
    return dict(source=source, data_kind='simulated_bank', currency='CNY', amount_unit='fen',
                as_of=utc_iso(utc_now()), data=data)


def paginate(items, arguments):
    offset = (arguments.page - 1) * arguments.page_size
    return dict(items=items[offset:offset + arguments.page_size], total=len(items),
                page=arguments.page, page_size=arguments.page_size,
                has_more=offset + arguments.page_size < len(items))


async def card_rows(context):
    items = [CardView.model_validate(row).model_dump(mode='json')
             for row in await card_service.list_cards(context.db, user_id=context.user_id)]
    for row in items:
        row['card_number'] = masked(row['card_number'])
        row['account_label'] = masked(row['account_label'])
    return items


async def subscription_rows(context):
    items = [SubscriptionView.model_validate(row).model_dump(mode='json')
             for row in await subscription_service.list_subscriptions(user_id=context.user_id, session=context.db)]
    for row in items:
        row.pop('amount')  # 仅提供分，避免元/分混用。
        row['debit_account_label'] = masked(row['debit_account_label'])
    return items


async def list_cards(arguments, context):
    items = await card_rows(context)
    for key in ('status', 'card_type', 'is_virtual'):
        value = getattr(arguments, key)
        if value is not None:
            items = [row for row in items if row[key] == value]
    if arguments.last_four is not None:
        items = [row for row in items if row['card_number'].endswith(arguments.last_four)]
    data = paginate(items, arguments)
    data['filters'] = arguments.model_dump(exclude_none=True, exclude={'page', 'page_size'})
    return envelope('cards.list', data)


async def get_card(arguments, context):
    key = arguments.card_id or (context.page.cards.selected_card_id if context.page.cards else None)
    if key is None:
        return dict(error='MISSING_SELECTION', message='请指定卡片 ID，或在卡片页选择一张卡片。')
    row = next((row for row in await card_rows(context) if row['card_id'] == key), None)
    if row is None:
        raise BankError('卡片不存在', 'CARD_NOT_FOUND', 404)
    return envelope('cards.detail', row)


async def list_card_transactions(arguments, context):
    key = arguments.card_id or (context.page.cards.selected_card_id if context.page.cards else None)
    if key is None:
        return dict(error='MISSING_SELECTION', message='请指定卡片 ID，或在卡片页选择一张卡片。')
    rows = await card_service.list_card_authorizations(context.db, user_id=context.user_id, card_id=key)
    items = [dict(authorized_at=utc_iso(as_utc(row['authorized_at'], legacy_utc=True)),
                  merchant=row['merchant'], channel=row['channel'], amount_fen=row['amount_fen'],
                  approved=row['approved'], decline_code=row['decline_code'], message=row['message'])
             for row in rows]
    if arguments.approved is not None:
        items = [row for row in items if row['approved'] == arguments.approved]
    card = next((row for row in await card_rows(context) if row['card_id'] == key), None)
    return envelope('cards.authorizations', dict(
        card_id=key, card_number=card['card_number'] if card else None, items=items, total=len(items),
        scope='最近20笔用卡授权（含被拒绝交易）', filters=arguments.model_dump(exclude_none=True)))


# Bank rule rejections the user can fix by editing values in the chat form.
FORM_FIXABLE_ERRORS = {
    'TXN_LIMIT_OUT_OF_RANGE', 'TXN_LIMIT_INCONSISTENT', 'TXN_LIMIT_UNCHANGED',
    'CREDIT_LIMIT_OUT_OF_RANGE', 'CREDIT_LIMIT_UNCHANGED', 'CREDIT_LIMIT_BELOW_USED', 'CHANNELS_UNCHANGED',
}
FIELD_LABELS = {'card_id': '目标卡片', 'account_id': '关联借记账户', 'credit_limit': '新信用额度',
                'single_txn_limit': '单笔限额', 'daily_txn_limit': '单日限额', **{name: '渠道开关' for name in CHANNEL_FIELDS}}


class CardTaskError(Exception):
    def __init__(self, message: str, code: str, status_code: int = 409):
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


async def _owned_session(db, user_id: str, session_id: str, *, create: bool = False):
    row = await db.get(NovaSessionORM, session_id, with_for_update=True, populate_existing=True)
    if row is not None and row.user_id != user_id:
        raise CardTaskError('会话不存在', 'SESSION_NOT_FOUND', 404)
    if row is None and create:
        row = NovaSessionORM(session_id=session_id, user_id=user_id, version=0,
                             state={'tasks': [], 'focus_task_id': None})
        db.add(row)
    return row


def _entries(state: dict) -> list[dict]:
    # Earlier entries stored only an operation reference; their action is read from the operation.
    return [{'entry_id': item.get('entry_id') or item['operation_id'], 'prefill': {}, 'notice': None,
             'closed': False, **item} for item in state.get('card_operations', [])]


async def register_chat_card_task(context, *, action: str, card_id: str | None, prefill: dict,
                                  notice: str | None = None, operation_id: str | None = None) -> None:
    """Show a card task in the conversation: an editable form, or a preview awaiting the user's own confirmation.

    Only references and form prefill are stored; operation status always comes from the owner-scoped record.
    The transfer/collection version is not bumped because no draft content changes.
    """
    if not (context.session_id and context.request_id):
        return
    row = await _owned_session(context.db, context.user_id, context.session_id, create=True)
    state = json.loads(json.dumps(row.state or {}))
    entries = [item for item in _entries(state) if item.get('operation_id') != operation_id or operation_id is None]
    entries.append({'entry_id': uuid4().hex, 'origin_request_id': context.request_id, 'action': action,
                    'card_id': card_id, 'prefill': prefill, 'notice': notice,
                    'operation_id': operation_id, 'closed': False})
    state['card_operations'] = entries[-MAX_CHAT_CARD_OPERATIONS:]
    row.state = state
    await context.db.commit()


async def chat_card_operations(db, user_id: str, state: dict) -> list[dict]:
    cards = {row['card_id']: row for row in [CardView.model_validate(item).model_dump(mode='json')
             for item in await card_service.list_cards(db, user_id=user_id)]}
    views = []
    for entry in _entries(state):
        if entry['closed']:
            continue
        operation = None
        if entry.get('operation_id'):
            row = await db.get(CardOperationORM, entry['operation_id'])
            if row is None or row.user_id != user_id:
                continue
            operation = operation_view(row).model_dump(mode='json')
        action = entry.get('action') or (operation or {}).get('action')
        card_id = entry.get('card_id') or (operation or {}).get('card_id')
        views.append({'entry_id': entry['entry_id'], 'origin_request_id': entry['origin_request_id'],
                      'action': action, 'card_id': card_id, 'card': cards.get(card_id),
                      'prefill': entry['prefill'], 'notice': entry['notice'], 'operation': operation})
    return views


async def link_chat_card_operation(db, *, user_id: str, session_id: str, entry_id: str, operation_id: str) -> list[dict]:
    """Attach a preview the user generated from the chat form; an unconfirmed older preview is cancelled."""
    from ...integrations.card_authorization import AWAITING_CONFIRMATION, AWAITING_MFA, cancel
    row = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(row.state or {})) if row else {}
    entries = _entries(state)
    entry = next((item for item in entries if item['entry_id'] == entry_id and not item['closed']), None)
    operation = await db.get(CardOperationORM, operation_id, populate_existing=True)
    if entry is None or operation is None or operation.user_id != user_id:
        raise CardTaskError('卡片任务不存在', 'CARD_TASK_NOT_FOUND', 404)
    if operation.action != entry['action'] or (entry.get('card_id') and operation.card_id != entry['card_id']):
        raise CardTaskError('预览与这张卡片任务不一致', 'CARD_TASK_MISMATCH', 422)
    if operation.status not in {AWAITING_CONFIRMATION, AWAITING_MFA}:
        raise CardTaskError('预览已结束，请重新生成', 'CARD_TASK_STALE', 409)
    previous_id = entry.get('operation_id')
    if previous_id and previous_id != operation_id:
        previous = await db.get(CardOperationORM, previous_id, with_for_update=True, populate_existing=True)
        if previous and previous.status == AWAITING_MFA:
            raise CardTaskError('原操作已本人确认，请先完成验证或取消原操作', 'CARD_TASK_CONFIRMED', 409)
        if previous and previous.status == AWAITING_CONFIRMATION:
            cancel(db, previous)
    entry.update(operation_id=operation_id, card_id=operation.card_id, notice=None)
    state['card_operations'] = entries
    row.state = state
    from types import SimpleNamespace
    from . import task_flow
    ctx = SimpleNamespace(db=db, user_id=user_id, session_id=session_id)
    flow_row, data = await task_flow.load(ctx)
    active = data.get('active') or {}
    if not active or (active.get('type') == 'card' and active.get('id') in {previous_id, operation_id}):
        data['active'] = {'type': 'card', 'id': operation_id}
        await task_flow.save(ctx, flow_row, data)
    await db.commit()
    return await chat_card_operations(db, user_id, state)


async def dismiss_chat_card_task(db, *, user_id: str, session_id: str, entry_id: str) -> list[dict]:
    from ...integrations.card_authorization import AWAITING_CONFIRMATION, cancel
    row = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(row.state or {})) if row else {}
    entries = _entries(state)
    entry = next((item for item in entries if item['entry_id'] == entry_id), None)
    if entry is None:
        raise CardTaskError('卡片任务不存在', 'CARD_TASK_NOT_FOUND', 404)
    if entry.get('operation_id'):
        operation = await db.get(CardOperationORM, entry['operation_id'], with_for_update=True, populate_existing=True)
        if operation and operation.status == AWAITING_CONFIRMATION:
            cancel(db, operation)
        elif operation and operation.status not in {'COMPLETED', 'CANCELLED', 'EXPIRED', 'FAILED'}:
            raise CardTaskError('原操作已本人确认，请先完成验证或取消原操作', 'CARD_TASK_CONFIRMED', 409)
    entry['closed'] = True
    state['card_operations'] = entries
    row.state = state
    await db.commit()
    return await chat_card_operations(db, user_id, state)


def _masked_change(change):
    if change['field'] != 'account_id':
        return change
    return dict(change, **{key: masked(change[key]) for key in ('before', 'after') if change[key]})


async def preview_card_operation(arguments, context):
    """开出与卡片页同一套校验的待确认单；信息不全或数值需调整时在聊天中打开同结构的表单。

    确认、强验证只能由本人在聊天中的卡片任务卡完成。
    """
    values = arguments.model_dump(exclude_none=True)
    values.pop('value_quotes',None)
    action = values['action']
    if action != 'card_apply_virtual' and 'card_id' not in values:
        selected = context.page.cards.selected_card_id if context.page.cards else None
        if selected is not None:
            values['card_id'] = selected
    # 用户只说“关掉境外”或只改一项限额时，其余字段沿用银行当前值；摘要仍绑定完整参数。
    if action in ('card_set_channels', 'card_set_transaction_limits') and 'card_id' in values:
        current = next((row for row in await card_rows(context) if row['card_id'] == values['card_id']), None)
        if current is None:
            raise BankError('卡片不存在', 'CARD_NOT_FOUND', 404)
        if action == 'card_set_channels':
            for name in CHANNEL_FIELDS:
                values.setdefault(name, current[name])
        else:
            for name in ('single_txn_limit', 'daily_txn_limit'):
                values.setdefault(name, f"{current[name + '_fen'] / 100:.2f}")
    required = ACTION_FIELDS[action]
    if any(key not in required for key in values if key != 'action'):
        return dict(error='INVALID_ARGUMENTS',
                    message=f"{action} 需要且只接受字段：{', '.join(sorted(required))}")
    prefill = {key: value for key, value in values.items() if key not in {'action', 'card_id'}}
    missing = sorted(required - set(values))
    if missing:
        return await _open_form(context, action, values.get('card_id'), prefill, missing, None)
    try:
        request = CardOperationCreate(operation_id=f'NOVA-{uuid4().hex}', **values)
    except ValidationError:
        return dict(error='INVALID_ARGUMENTS',
                    message=f"{action} 需要且只接受字段：{', '.join(sorted(required))}")
    try:
        row = await open_preview(context.db, user_id=context.user_id, request=request)
        await context.db.commit()
    except (BankError, CardAuthorizationError) as error:
        if getattr(error, 'persist', False):
            await context.db.commit()
        else:
            await context.db.rollback()
        if error.code in FORM_FIXABLE_ERRORS:
            return await _open_form(context, action, values.get('card_id'), prefill, [], error.message)
        return dict(error=error.code, message=error.message)
    await register_chat_card_task(context, action=action, card_id=row.card_id, prefill=prefill,
                                  operation_id=row.operation_id)
    card = row.preview.get('card') or {}
    red = row.permission_level == 'red'
    return envelope('cards.operation_preview', dict(
        operation_id=row.operation_id, action=row.action, action_label=ACTION_LABELS[row.action],
        status=row.status, permission_level=row.permission_level, requires_mfa=red, executed=False,
        card_id=row.card_id, card_number=masked(card['card_number']) if card.get('card_number') else None,
        changes=[_masked_change(change) for change in row.preview.get('changes', [])],
        expires_at=utc_iso(as_utc(row.expires_at, legacy_utc=True)),
        next_step='尚未执行。请在聊天中的卡片任务卡核对银行返回的变更并本人确认' + ('，再输入验证码完成强验证' if red else '')
                  + '；需要调整可在任务卡中修改；NOVA 不能代为确认或输入验证码。'))


async def _open_form(context, action, card_id, prefill, missing, bank_message):
    """信息不全或银行要求调整数值时，在聊天中打开与卡片页同结构的表单，由本人补充后生成预览。"""
    card = next((row for row in await card_rows(context) if row['card_id'] == card_id), None) if card_id else None
    if card_id and card is None:
        raise BankError('卡片不存在', 'CARD_NOT_FOUND', 404)
    await register_chat_card_task(context, action=action, card_id=card_id, prefill=prefill, notice=bank_message)
    labels = list(dict.fromkeys(FIELD_LABELS[name] for name in missing))
    return envelope('cards.operation_form', dict(
        form_opened=True, executed=False, action=action, action_label=ACTION_LABELS[action],
        card_id=card_id, card_number=card['card_number'] if card else None,
        missing_fields=labels, bank_message=bank_message,
        next_step='尚未生成预览。请在聊天中的“' + ACTION_LABELS[action] + '”表单'
                  + ('补充' + '、'.join(labels) if labels else '按银行提示调整数值')
                  + '，再生成确认信息并由本人确认；NOVA 不能代为确认或输入验证码。'))


async def list_subscriptions(arguments, context):
    items = await subscription_rows(context)
    if arguments.status is not None:
        items = [row for row in items if row['status'] == arguments.status]
    if arguments.keyword is not None:
        items = [row for row in items if arguments.keyword.casefold() in row['merchant_name'].casefold()]
    data = paginate(items, arguments)
    active = [row for row in items if row['status'] == 'ACTIVE']
    data['summary'] = dict(active_count=len(active), cancelled_count=sum(row['status'] == 'CANCELLED' for row in items),
        monthly_commitment_fen=sum(row['amount_fen'] for row in active if row['billing_cycle'] == 'MONTHLY' and row['currency'] == 'CNY'),
        scope='filtered_all_pages', basis='ACTIVE / MONTHLY / CNY; planned recurring amount, not actual charges')
    data['filters'] = arguments.model_dump(exclude_none=True, exclude={'page', 'page_size'})
    return envelope('subscriptions.list', data)


async def get_subscription(arguments, context):
    key = arguments.subscription_id or (context.page.subscriptions.selected_subscription_id if context.page.subscriptions else None)
    if key is None:
        return dict(error='MISSING_SELECTION', message='请指定订阅 ID，或在订阅页选择一项订阅。')
    row = next((row for row in await subscription_rows(context) if row['subscription_id'] == key), None)
    if row is None:
        raise BankError('订阅不存在', 'SUBSCRIPTION_NOT_FOUND', 404)
    return envelope('subscriptions.detail', row)


def _subscription_entries(state: dict) -> list[dict]:
    return [dict(item) for item in state.get('subscription_tasks', [])]


async def _owned_subscription(context_or_db, user_id: str, subscription_id: str) -> dict | None:
    rows = await subscription_service.list_subscriptions(user_id=user_id, session=context_or_db)
    row = next((item for item in rows if item['subscription_id'] == subscription_id), None)
    return SubscriptionView.model_validate(row).model_dump(mode='json') if row else None


async def request_subscription_cancellation(arguments, context):
    """在聊天中显示取消订阅任务卡；确认凭证只发给本人页面，取消须本人在任务卡中确认。"""
    key = arguments.subscription_id or (context.page.subscriptions.selected_subscription_id if context.page.subscriptions else None)
    subscription = None
    if key is not None:
        subscription = await _owned_subscription(context.db, context.user_id, key)
        if subscription is None:
            raise BankError('订阅不存在', 'SUBSCRIPTION_NOT_FOUND', 404)
        if subscription['status'] != 'ACTIVE':
            return dict(error='SUBSCRIPTION_NOT_ACTIVE', message='该订阅已取消，无需再次取消。')
    if context.session_id and context.request_id:
        row = await _owned_session(context.db, context.user_id, context.session_id, create=True)
        state = json.loads(json.dumps(row.state or {}))
        entries = [item for item in _subscription_entries(state)
                   if item['closed'] or key is None or item.get('subscription_id') != key]
        entries.append({'entry_id': uuid4().hex, 'origin_request_id': context.request_id,
                        'subscription_id': key, 'closed': False})
        state['subscription_tasks'] = entries[-MAX_CHAT_CARD_OPERATIONS:]
        row.state = state
        await context.db.commit()
    data = dict(executed=False, form_opened=subscription is None,
                next_step='尚未取消。请在聊天中的取消订阅任务卡' + ('选择要取消的订阅，' if subscription is None else '')
                          + '查看取消详情并由本人确认；NOVA 不能代为确认。')
    if subscription:
        data.update({k: subscription[k] for k in ('subscription_id', 'merchant_name', 'amount_fen', 'currency',
                                                   'billing_cycle', 'next_charge_at', 'status')})
    return envelope('subscriptions.cancel_request', data)


async def chat_subscription_tasks(db, user_id: str, state: dict) -> list[dict]:
    views = []
    for entry in _subscription_entries(state):
        if entry['closed']:
            continue
        subscription = await _owned_subscription(db, user_id, entry['subscription_id']) if entry.get('subscription_id') else None
        views.append({'entry_id': entry['entry_id'], 'origin_request_id': entry['origin_request_id'],
                      'subscription_id': entry.get('subscription_id'), 'subscription': subscription})
    return views


async def update_chat_subscription_task(db, *, user_id: str, session_id: str, entry_id: str,
                                        subscription_id: str | None = None, close: bool = False) -> list[dict]:
    """本人在任务卡中选择订阅或关闭任务卡；取消本身只走订阅确认接口。"""
    row = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(row.state or {})) if row else {}
    entries = _subscription_entries(state)
    entry = next((item for item in entries if item['entry_id'] == entry_id and not item['closed']), None)
    if entry is None:
        raise CardTaskError('订阅任务不存在', 'SUBSCRIPTION_TASK_NOT_FOUND', 404)
    if close:
        entry['closed'] = True
    else:
        subscription = await _owned_subscription(db, user_id, subscription_id or '')
        if subscription is None:
            raise CardTaskError('订阅不存在', 'SUBSCRIPTION_NOT_FOUND', 404)
        if subscription['status'] != 'ACTIVE':
            raise CardTaskError('该订阅已取消，无需再次取消', 'SUBSCRIPTION_NOT_ACTIVE', 409)
        entry['subscription_id'] = subscription_id
    state['subscription_tasks'] = entries
    row.state = state
    await db.commit()
    return await chat_subscription_tasks(db, user_id, state)


def card_subscription_tools():
    entries = [
        ('list_cards', CardListArguments, list_cards, '分页查询本人银行卡片，可按状态（ACTIVE/LOCKED/LOST/CANCELLED）、借记/信用、虚拟卡、卡号末四位筛选。返回脱敏卡号、关联账户、信用额度及已用/可用信用额度、交易限额及银行上限、有效期和渠道开关。信用额度不是现金。'),
        ('get_card', CardArguments, get_card, '查询本人卡片详情。省略 card_id 沿用 cards.selected_card_id。未选择先询问；按末四位查询先 list_cards，多个匹配先询问。只读。'),
        ('list_card_transactions', CardTransactionArguments, list_card_transactions, '查询本人某张卡最近20笔用卡授权记录（含被拒绝的交易及拒绝原因，如超限额、渠道关闭、卡片锁定）。省略 card_id 沿用 cards.selected_card_id；按末四位先 list_cards。只读。'),
        ('preview_card_operation', CardPreviewArguments, preview_card_operation, '为本人卡片操作生成待核对预览，不会执行。action：card_apply_virtual（传 account_id）；card_lock/card_unlock/card_report_lost/card_cancel_virtual/card_reissue（传 card_id）；card_set_transaction_limits（single_txn_limit/daily_txn_limit，元，未提及的一项沿用当前值）；card_adjust_credit_limit（credit_limit，元）；card_set_channels（online_enabled/atm_enabled/overseas_enabled/contactless_enabled，未提及的沿用当前值）。省略 card_id 沿用 cards.selected_card_id。金额取用户原话，不得猜测。缺少目标卡、账户或金额时不要编造：直接调用本工具，聊天中会打开同结构的表单让用户补充；银行要求调整数值时同样打开表单。返回 executed=false：用户须在聊天中的卡片任务卡本人确认，red 还需验证码；NOVA 不能代为确认。'),
        ('request_subscription_cancellation', SubscriptionCancelArguments, request_subscription_cancellation, '用户要取消本人订阅时调用：在聊天中显示取消订阅任务卡，不会取消。省略 subscription_id 沿用 subscriptions.selected_subscription_id；按商户名先 list_subscriptions 定位，多个匹配先询问；用户没说是哪一项时不传，任务卡中会列出生效订阅供选择。已取消的订阅不能再取消。返回 executed=false：用户须在任务卡中查看取消详情并本人确认；NOVA 不能代为确认。'),
        ('list_subscriptions', SubscriptionListArguments, list_subscriptions, '分页查询本人订阅，可筛选 ACTIVE/CANCELLED 和商户 keyword。summary 是筛选后所有分页的生效订阅每月预计支出，仅 MONTHLY/CNY，非实际扣款。实际扣费请查询账单流水。'),
        ('get_subscription', SubscriptionArguments, get_subscription, '查询本人订阅详情：商户、周期金额、扣费账户、状态、计划下次扣费、取消时间及识别来源流水。省略 subscription_id 沿用 subscriptions.selected_subscription_id；按商户名先 list_subscriptions，多匹配先询问。不取消、不扣费。'),
    ]
    return {name: {'schema': {'type': 'function', 'function': {
        'name': name, 'description': description, 'parameters': model.model_json_schema()}},
        'arguments': model, 'execute': execute, 'requires_identity': True}
        for name, model, execute, description in entries}


async def offline_reply(message, context, execute):
    if context is None:
        return None
    # 明确账单/流水的历史扣费问题仍交给账单工具；禁止把操作请求伪装成查询。
    operation_text = re.sub(r'已经取消|已取消|取消时间|取消日期|已挂失|挂失时间|挂失日期|已注销|注销时间|已锁定|已补卡', '', message)
    if re.search(r'取消|挂失|扣一下|模拟扣费|执行|修改|调整|申请|解锁|冻结|删除|账单|流水|消费|锁上|锁住|锁卡|锁定(这|该|此|我|卡)|补卡|注销|开通|关闭|打开|关掉|开启|提额|降额|调高|调低|调到|改成|设为', operation_text):
        return None
    domain = 'cards' if re.search(r'卡片|银行卡|信用卡|借记卡|虚拟卡|这张卡', message) else 'subscriptions' if '订阅' in message else None
    if domain is None:
        return None
    detail = bool(re.search(r'这张|这个|选中', message))
    name = ('get_card' if detail else 'list_cards') if domain == 'cards' else ('get_subscription' if detail else 'list_subscriptions')
    arguments = {}
    if not detail:
        if domain == 'cards':
            for word, value in [('信用', 'CREDIT'), ('借记', 'DEBIT')]:
                if word in message:
                    arguments['card_type'] = value
            if '虚拟' in message:
                arguments['is_virtual'] = True
            tail = re.search(r'(?:尾号|末四位)\s*(\d{4})(?!\d)', message)
            if tail:
                arguments['last_four'] = tail[1]
            for word, value in [('锁定', 'LOCKED'), ('正常', 'ACTIVE'), ('挂失', 'LOST'), ('注销', 'CANCELLED')]:
                if word in message:
                    arguments['status'] = value
        elif '已取消' in message:
            arguments['status'] = 'CANCELLED'
        elif re.search(r'生效|有效|每月', message):
            arguments['status'] = 'ACTIVE'
    result = await execute({'function': {'name': name, 'arguments': json.dumps(arguments)}}, context)
    if result.get('error'):
        reply = result['message']
    else:
        data = result['data']
        items = [data] if detail else data['items']
        labels = dict(ACTIVE='生效中', CANCELLED='已取消', LOCKED='已锁定', LOST='已挂失')
        lines = []
        for row in items[:10]:
            if domain == 'cards':
                credit = f"，信用额度 ¥{row['credit_limit_fen'] / 100:,.2f}" if row['credit_limit_fen'] is not None else ''
                lines.append(f"- {row['card_number']}（{row['card_id']}）：{'信用卡' if row['card_type'] == 'CREDIT' else '借记卡'}，{card_service.STATUS_LABELS[row['status']]}，单笔 ¥{row['single_txn_limit_fen'] / 100:,.2f}，单日 ¥{row['daily_txn_limit_fen'] / 100:,.2f}{credit}。")
            else:
                schedule = f"计划下次扣费 {row['next_charge_at']}（UTC）" if row['status'] == 'ACTIVE' else f"取消时间 {row['cancelled_at'] or '未提供'}"
                lines.append(f"- {row['merchant_name']}（{row['subscription_id']}）：¥{row['amount_fen'] / 100:,.2f} {row['currency']} / {row['billing_cycle']}，{labels[row['status']]}，{schedule}。")
        reply = f"模拟银行{'卡片' if domain == 'cards' else '订阅'}：{'详情' if detail else str(data['total']) + ' 项匹配结果'}。\n" + ('\n'.join(lines) or '没有匹配记录。')
        if not detail and (data['has_more'] or len(items) > 10):
            reply += '\n仅展示前10项，更多记录请继续分页查询。'
        if domain == 'subscriptions' and not detail:
            reply += f"\n筛选范围内生效月度人民币订阅预计每月 ¥{data['summary']['monthly_commitment_fen'] / 100:,.2f}，不是已发生扣款。"
    return dict(reply=reply, mode='offline', tool_calls=[dict(name=name, result=result)])
