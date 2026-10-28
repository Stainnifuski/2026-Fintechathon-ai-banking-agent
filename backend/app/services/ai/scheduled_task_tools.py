"""NOVA 自定义定时任务：信息完整时直接创建（可锁定活期资金），否则在聊天中打开预填表单。

开始执行、完成和取消由本人在任务卡中点击；NOVA 不代为开始或结束任务。
"""
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from ...domain.time import utc_iso, utc_now
from ...schemas.nova import StrictModel
from .. import scheduled_tasks as tasks
from ..bank import data_service

FIELD_LABELS = {'description': '任务内容', 'run_date': '执行日期', 'run_time': '执行时间'}
MAX_CHAT_SCHEDULED_TASKS = 30
YUAN_PATTERN = r'^\d{1,9}(\.\d{1,2})?$'


class ScheduledTaskArguments(StrictModel):
    action: Literal['create', 'show'] = Field(
        default='create', description='create=新建自定义定时任务；show=在聊天中显示已有任务的卡片，以便本人开始、完成或取消')
    task_id: str | None = Field(default=None, min_length=1, max_length=40,
                                description='show 时的任务 ID，取自 list_scheduled_tasks 结果')
    description: str | None = Field(default=None, min_length=1, max_length=2000,
                                    description='到时要让 NOVA 办理的事，用用户的意思完整表述，如“查询本月支出并帮我制定预算”')
    run_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$', description='执行日期 YYYY-MM-DD，按当前时间换算')
    run_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$', description='执行时间 HH:MM（24小时制）')
    timezone: str | None = Field(default=None, max_length=80)
    lock_amount: str | None = Field(default=None, pattern=YUAN_PATTERN,
                                    description='用户要求为任务锁定的活期金额（元）；没说就不填')
    lock_account_id: str | None = Field(default=None, min_length=1, max_length=32,
                                        description='锁定资金的本人活期账户 ID，取自账户查询结果；只有一个活期账户时可省略')

    @field_validator('run_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError('请使用合法 IANA 时区') from None
        return value


class ScheduledListArguments(StrictModel):
    include_completed: bool = False


def envelope(source, data):
    return dict(source=source, data_kind='user_schedule', as_of=utc_iso(utc_now()), data=data)


def _summary(view: dict) -> dict:
    lock = view.get('lock')
    return {key: view[key] for key in ('task_id', 'task_type', 'description', 'status', 'completed')} | {
        'scheduled_at': utc_iso(view['scheduled_at']),
        'lock_amount_fen': lock['amount_fen'] if lock else None,
        'lock_remaining_fen': lock['remaining_fen'] if lock else None}


async def _register(context, *, prefill: dict, notice: str | None, task_id: str | None) -> None:
    if not (context.session_id and context.request_id):
        return
    from .card_subscription_tools import _owned_session
    row = await _owned_session(context.db, context.user_id, context.session_id, create=True)
    state = json.loads(json.dumps(row.state or {}))
    entries = [item for item in state.get('scheduled_tasks', [])
               if task_id is None or item.get('task_id') != task_id]
    entries.append({'entry_id': uuid4().hex, 'origin_request_id': context.request_id, 'prefill': prefill,
                    'notice': notice, 'task_id': task_id, 'closed': False})
    state['scheduled_tasks'] = entries[-MAX_CHAT_SCHEDULED_TASKS:]
    row.state = state
    await context.db.commit()


async def list_scheduled_tasks(arguments, context):
    items = (await tasks.list_all(context.db, context.user_id))['items']
    if not arguments.include_completed:
        items = [item for item in items if not item['completed']]
    return envelope('scheduled_tasks.list', dict(items=[_summary(item) for item in items], total=len(items)))


async def manage_scheduled_task(arguments, context):
    if arguments.action == 'show':
        view = await tasks.owned_view(context.db, context.user_id, arguments.task_id or '')
        if view is None:
            return dict(error='SCHEDULED_TASK_NOT_FOUND', message='没有找到这个定时任务，请先查询定时任务列表。')
        await _register(context, prefill={}, notice=None, task_id=view['task_id'])
        return envelope('scheduled_tasks.card', dict(created=False, form_opened=False, task=_summary(view)))

    zone = arguments.timezone or (context.page.calendar.timezone if context.page.calendar else 'Asia/Shanghai')
    accounts = [item for item in await data_service.list_accounts(context.user_id, session=context.db)
                if item['account_type'] in {'savings', 'checking'} and item.get('status', 'active') == 'active']
    prefill = arguments.model_dump(exclude_none=True, include={'description', 'run_date', 'run_time', 'lock_amount'})
    notices = []
    if arguments.lock_amount is not None:
        account_ids = {item['account_id'] for item in accounts}
        account_id = arguments.lock_account_id or (accounts[0]['account_id'] if len(accounts) == 1 else None)
        if account_id in account_ids:
            prefill['lock_account_id'] = account_id
        else:
            notices.append('请在表单中选择锁定资金的活期账户。')
    run_at = None
    if 'run_date' in prefill and 'run_time' in prefill:
        run_at = datetime.combine(date.fromisoformat(prefill['run_date']), time.fromisoformat(prefill['run_time']), ZoneInfo(zone))
        if run_at <= utc_now():
            notices.append('执行时间已经过去，请重新选择。')
    missing = [key for key in ('description', 'run_date', 'run_time') if key not in prefill]
    lock_ready = arguments.lock_amount is None or 'lock_account_id' in prefill
    if not missing and not notices and lock_ready:
        try:
            view = await tasks.create_custom_task(
                context.db, user_id=context.user_id, description=prefill['description'], scheduled_at=run_at,
                lock_amount_fen=int(Decimal(prefill['lock_amount']) * 100) if 'lock_amount' in prefill else None,
                lock_account_id=prefill.get('lock_account_id'))
        except tasks.ScheduledTaskError as error:
            # 资金不足等业务拒绝：任务未保存，打开表单让本人调整金额或账户。
            notices.append(error.message + '。')
        else:
            await _register(context, prefill=prefill, notice=None, task_id=view['task_id'])
            return envelope('scheduled_tasks.created', dict(created=True, task=_summary(view),
                next_step='已创建；到时间后在任务卡点击“开始执行”，NOVA 才会按描述办理。'
                          + ('锁定的资金在任务结束前不能被其他付款使用。' if view['lock'] else '')))
    notice = ''.join(notices) or None
    await _register(context, prefill=prefill, notice=notice, task_id=None)
    return envelope('scheduled_tasks.form', dict(
        created=False, form_opened=True, prefill=prefill, notice=notice,
        missing_fields=[FIELD_LABELS[key] for key in missing],
        next_step='尚未创建。请在聊天中的定时任务表单补全后保存。'))


async def chat_scheduled_tasks(db, user_id: str, state: dict) -> list[dict]:
    views = []
    for entry in state.get('scheduled_tasks', []):
        if entry.get('closed'):
            continue
        task = await tasks.owned_view(db, user_id, entry['task_id']) if entry.get('task_id') else None
        if entry.get('task_id') and task is None:
            continue
        views.append({'entry_id': entry['entry_id'], 'origin_request_id': entry['origin_request_id'],
                      'prefill': entry['prefill'], 'notice': entry.get('notice'), 'task': task})
    return views


async def update_chat_scheduled_task(db, *, user_id: str, session_id: str, entry_id: str,
                                     task_id: str | None = None, close: bool = False) -> list[dict]:
    """把本人在聊天表单中保存的任务挂到任务卡，或关闭未保存的表单。"""
    from .card_subscription_tools import CardTaskError, _owned_session
    row = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(row.state or {})) if row else {}
    entries = list(state.get('scheduled_tasks', []))
    entry = next((item for item in entries if item['entry_id'] == entry_id and not item.get('closed')), None)
    if entry is None:
        raise CardTaskError('定时任务卡不存在', 'SCHEDULED_CARD_NOT_FOUND', 404)
    if close:
        entry['closed'] = True
    else:
        if await tasks.owned_view(db, user_id, task_id or '') is None:
            raise CardTaskError('定时任务不存在', 'SCHEDULED_TASK_NOT_FOUND', 404)
        entry.update(task_id=task_id, notice=None)
    state['scheduled_tasks'] = entries
    row.state = state
    await db.commit()
    return await chat_scheduled_tasks(db, user_id, state)


def scheduled_task_tools():
    entries = [
        ('list_scheduled_tasks', ScheduledListArguments, list_scheduled_tasks,
         '只读查询本人定时任务（自定义任务和定时转账）：内容、执行时间、状态及锁定资金。默认只列未完成的。'),
        ('manage_scheduled_task', ScheduledTaskArguments, manage_scheduled_task,
         '新建自定义定时任务，或显示已有任务卡。create：按语义填写任务内容、执行日期和时间（按当前时间换算），用户要求锁定资金时填写锁定金额和账户。'
         '信息齐全且时间在未来时直接创建；锁定金额会立刻从活期锁定，可自由支配余额不足则不创建并打开表单。缺项时打开预填表单。'
         '定时转账不用本工具，用 manage_transfer_request 的定时模式。开始执行、完成、取消须由本人在任务卡点击；要处理已有任务时用 show 打开它的卡片。'),
    ]
    return {name: dict(schema={'type': 'function', 'function': dict(name=name, description=description,
        parameters=model.model_json_schema())}, arguments=model, execute=function, requires_identity=True)
        for name, model, function, description in entries}
