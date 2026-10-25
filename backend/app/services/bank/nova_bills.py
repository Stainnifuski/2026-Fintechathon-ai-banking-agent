"""Bill preference previews and trusted confirmation share the existing bank services.

Preparing validates in a rolled-back savepoint. Only a user clicking the owned,
unexpired card can commit; a model has no confirmation tool.
"""
import hashlib
import json
from datetime import timedelta
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from ...domain.time import utc_now
from ...models.nova_bill_action import NovaBillActionORM as Action
from ...models.nova_conversation import NovaConversationORM
from ...schemas.nova_bills import PrepareBillChange
from . import bill_foundation as foundation, bill_classification as classification
from . import bill_budgets as budgets, bill_reviews as reviews, bill_schedules as schedules
from . import bill_forecasts as forecasts, bill_merchants as merchants, bill_recurring as recurring
from . import nova_conversations as conversations, nova_reads
from .transfer_service import BankError

CATEGORIES = {'dining':'餐饮','shopping':'购物','transport':'交通','entertainment':'娱乐',
              'subscription':'订阅','education':'学习','other':'其他'}
TITLES = {'tags':'编辑流水标签','classification':'修正消费分类','remove_category_rule':'撤销商户分类规则',
          'budget':'设置月度预算','anomaly_review':'保存异常核对','recurring_plan':'设置周期收支计划',
          'recurring_enabled':'调整周期预测','merchant_alias':'添加商户别名','merchant_merge':'归并商户',
          'merchant_split':'拆分商户','merchant_undo':'撤销商户归并','save_forecast':'保存基准预测',
          'refresh_foundations':'刷新账单关联与周期识别'}


async def owned_session(db, user_id, session_id):
    row = await db.get(NovaConversationORM, session_id)
    if not row or row.user_id != user_id:
        raise BankError('对话不存在或无权访问', 'BILL_SESSION_NOT_FOUND', 404)


def unpack(row):
    return json.loads(conversations._decrypt(row.payload_ciphertext))


def public(row):
    payload = unpack(row)
    status = row.status
    if status == 'pending' and foundation.utc(row.expires_at) <= utc_now():
        status = 'expired'
    return dict(action_id=row.action_id, request_id=row.request_id, session_id=row.session_id,
                status=status, title=payload['title'], lines=payload['lines'],
                expires_at=foundation.utc(row.expires_at).isoformat(),
                notice='仅修改本人的账单整理和预测设置，不转账、不退款、不取消订阅或执行扣费。')


async def cards(db, user_id, session_id):
    await owned_session(db, user_id, session_id)
    rows = (await db.scalars(select(Action).where(Action.user_id == user_id, Action.session_id == session_id)
                            .order_by(Action.created_at.desc(), Action.action_id).limit(100))).all()
    return [public(row) for row in reversed(rows)]


async def pending_context(db, user_id, session_id):
    """Internal task-only sessions may predate conversations; absence is empty."""
    rows = (await db.scalars(select(Action).where(Action.user_id == user_id,
        Action.session_id == session_id, Action.status == 'pending', Action.expires_at > utc_now())
        .order_by(Action.created_at.desc(), Action.action_id).limit(10))).all()
    return [public(row) for row in reversed(rows)]


async def apply(db, user_id, change):
    """No commits here: confirmation and its receipt commit atomically."""
    kind = change.action
    if kind == 'tags':
        return await foundation.annotate(db, user_id, change.tx_id, change)
    if kind == 'classification':
        return await classification.correct(db, user_id, change.tx_id, change)
    if kind == 'remove_category_rule':
        return await classification.remove_rule(db, user_id, change.merchant_id, change.expected_version)
    if kind == 'budget':
        return await budgets.save(db, user_id, change)
    if kind == 'anomaly_review':
        return await reviews.save_review(db, user_id, change)
    if kind == 'recurring_plan':
        return await schedules.save_plan(db, user_id, change, change.stream_id)
    if kind == 'recurring_enabled':
        return await recurring.set_enabled(db, user_id, change.stream_id, change)
    if kind == 'merchant_alias':
        return await foundation.add_alias(db, user_id, change.merchant_id, change.name)
    if kind == 'merchant_merge':
        return await merchants.merge(db, user_id, change.merchant_id, change.source_merchant_id, change.preview_digest)
    if kind == 'merchant_split':
        return await merchants.split(db, user_id, change.merchant_id, change)
    if kind == 'merchant_undo':
        return await merchants.undo(db, user_id, change.event_id)
    if kind == 'save_forecast':
        return await forecasts.save(db, user_id, change.scenario)
    if kind == 'refresh_foundations':
        return (await foundation.sync_users(db, [user_id]))[user_id]
    raise BankError('不支持的账单操作', 'INVALID_BILL_ACTION', 422)


def yuan(value):
    from decimal import Decimal
    return f'¥{Decimal(value)/100:,.2f}'


async def describe(db, user_id, change):
    """Only server-resolved names and explicit, typed preferences appear on the card."""
    kind = change.action
    lines = []
    if kind in {'tags', 'classification'}:
        row = await nova_reads.transaction_detail(db, user_id, change.tx_id)
        lines.append(f"流水：{row.get('merchant_name') or row['counterparty']} · {yuan(row['amount_fen'])} · {row['timestamp']}")
        if kind == 'tags':
            lines += ['原标签：' + ('、'.join(t['name'] for t in row.get('tags', [])) or '无'),
                      '新标签（完整替换）：' + ('、'.join(change.tags) or '清空')]
        else:
            lines += ['原分类：' + CATEGORIES.get(row.get('category'), '其他'),
                      '新分类：' + (CATEGORIES[change.category] if change.category else '清除单笔修正，沿用商户规则或原始分类'),
                      '同时应用到本商户其他消费：' + ('是' if change.apply_to_merchant else '否')]
    elif kind == 'budget':
        current = await budgets.summary(db, user_id, change.month)
        old = next((r for r in current['items'] if r['account_id'] == change.account_id and r['category'] == change.category), None)
        lines = [f"账期：{change.month}", '范围：' + (CATEGORIES[change.category] if change.category else '总预算'),
                 '原预算：' + (yuan(old['amount_fen']) if old else '未设置'), '新预算：' + yuan(change.amount_fen),
                 f'达到 {change.warning_percent}% 时预警', '预算状态：' + ('启用' if change.active else '停用')]
    elif kind == 'anomaly_review':
        from .bill_service import analyze
        report = await analyze(db, user_id, change.query, now=change.as_of)
        item = next((r for r in report['anomalies'] if r['anomaly_id'] == change.anomaly_id), None)
        if not item:
            raise BankError('异常依据已变化，请重新查询', 'ANOMALY_CHANGED', 409)
        labels = {'pending':'待核对','normal':'正常','resolved':'已处理'}
        lines = [f"账期：{report['query']['date']}", '异常：' + item['title'],
                 f"状态：{labels[item['review_status']]} → {labels[change.status]}", '备注：' + (change.note or '无')]
    elif kind in {'recurring_plan', 'recurring_enabled'}:
        if change.stream_id:
            rows = (await recurring.list_recurring(db, user_id))['items']
            old = next((r for r in rows if r['stream_id'] == change.stream_id), None)
            if not old:
                raise BankError('周期记录不存在', 'RECURRING_NOT_FOUND', 404)
            lines.append(f"现有周期：{old['merchant']} · {yuan(old['amount_fen'])} · {'纳入预测' if old['enabled'] else '未纳入预测'}")
        if kind == 'recurring_plan':
            freq = {'WEEKLY':'每周','MONTHLY':'每月','QUARTERLY':'每季度','YEARLY':'每年'}
            lines += [f"新设置：{change.name} · {'收入' if change.kind == 'income' else '支出'} · {yuan(change.amount_fen)} / {freq[change.frequency]}",
                      '起始时间：' + change.anchor_at.isoformat(), '固定月末：' + ('是' if change.month_end else '否'),
                      '分类：' + CATEGORIES[change.category], '计划状态：' + ('有效' if change.active else '取消')]
        lines.append('纳入预测：' + ('是' if change.enabled else '否'))
    elif kind in {'merchant_alias', 'remove_category_rule', 'merchant_merge', 'merchant_split'}:
        state = await merchants.state(db, user_id, change.merchant_id)
        lines.append('商户：' + state['name'])
        if kind == 'merchant_alias':
            lines.append('添加别名：' + change.name)
        elif kind == 'remove_category_rule':
            rule = next((r for r in await classification.rules(db, user_id) if r['merchant_id'] == change.merchant_id), None)
            if rule:
                lines.append('移除规则：' + CATEGORIES[rule['category']] + '；无单笔修正的消费将回到原始分类')
        elif kind == 'merchant_merge':
            preview = await merchants.preview(db, user_id, change.merchant_id, change.source_merchant_id)
            lines += [f"归入商户：{preview['source']} → {preview['target']}",
                      f"移动 {preview['transaction_count']} 笔流水和 {preview['alias_count']} 个别名", *preview['notices']]
        else:
            preview = await merchants.split_preview(db, user_id, change.merchant_id, change)
            lines += [f'拆到新商户：{change.name}', f"移动 {preview['moving_count']} 笔流水（包含关联原单与退款）",
                      '移动别名：' + ('、'.join(change.aliases) or '无'), *preview['notices']]
    elif kind == 'merchant_undo':
        event = next((r for r in await merchants.history(db, user_id) if r['event_id'] == change.event_id), None)
        if not event:
            raise BankError('归并记录不存在或超出最近记录范围', 'MERGE_NOT_FOUND', 404)
        lines = [f"撤销归并：{event['source_name']} → {event['target_name']}", '恢复原商户关联；结构已变化时拒绝执行。']
    elif kind == 'save_forecast':
        lines = [f'保存未来 {change.scenario.days} 天的基准预测，供到期后与实际收支复盘。', '保存时按最新余额及数据计算；不保存临时假设情景。']
    else:
        lines = ['重新核验本人的历史退款、互转关联及周期模式；证据不足的记录保持未关联。']
    account_id = getattr(change, 'account_id', None)
    if kind == 'save_forecast':
        account_id = change.scenario.account_id
    if kind in {'budget','recurring_plan','save_forecast'}:
        if account_id:
            from ...models.account import AccountORM
            account = await db.get(AccountORM, account_id)
            if not account or account_id not in await foundation.account_ids(db, user_id):
                raise BankError('账户不存在', 'ACCOUNT_NOT_FOUND', 404)
            lines.append('账户：尾号 ' + account.card_number[-4:])
        else:
            lines.append('账户：本人全部授权账户' if kind == 'budget' else '账户：本人全部有效储蓄账户')
    return lines


async def prepare(db, user_id, session_id, request_id, arguments, selected_tx_id=None):
    await owned_session(db, user_id, session_id)
    change = arguments.change
    if change.action in {'tags','classification'} and change.tx_id is None:
        if not selected_tx_id:
            raise BankError('请先选择具体流水', 'MISSING_SELECTION', 422)
        change = change.model_copy(update={'tx_id': selected_tx_id})
        arguments = arguments.model_copy(update={'change': change})
    encoded = arguments.model_dump_json()
    action_id = 'NB-' + hashlib.sha256((user_id + ':' + session_id + ':' + request_id + ':' + encoded).encode()).hexdigest()[:32]
    await foundation.lock_users(db, [user_id])
    existing = await db.get(Action, action_id, populate_existing=True)
    if existing:
        return public(existing)
    lines = await describe(db, user_id, change)
    # Shared business validators run now and again at confirmation. No preview
    # writes, metadata events, or forecasts escape this savepoint.
    savepoint = await db.begin_nested()
    try:
        await apply(db, user_id, change)
    finally:
        await savepoint.rollback()
    row = Action(action_id=action_id, user_id=user_id, session_id=session_id, request_id=request_id,
                 status='pending', created_at=utc_now(), expires_at=utc_now() + timedelta(minutes=15),
                 payload_ciphertext=conversations._encrypt(json.dumps({
                     'arguments':json.loads(encoded), 'title':TITLES[change.action], 'lines':lines,
                 }, ensure_ascii=False)))
    db.add(row)
    await db.commit()
    return public(row)


async def decide(db, user_id, session_id, action_id, decision):
    await owned_session(db, user_id, session_id)
    await foundation.lock_users(db, [user_id])
    row = await db.scalar(select(Action).where(Action.action_id == action_id,
        Action.user_id == user_id, Action.session_id == session_id).with_for_update().execution_options(populate_existing=True))
    if not row:
        raise BankError('账单确认卡不存在', 'BILL_ACTION_NOT_FOUND', 404)
    if row.status != 'pending':
        if (row.status == 'completed' and decision == 'confirm') or (row.status == 'cancelled' and decision == 'cancel'):
            return public(row)
        raise BankError('该卡片已处理，请重新查询', 'BILL_ACTION_FINISHED', 409)
    if decision == 'cancel':
        row.status = 'cancelled'
    else:
        if foundation.utc(row.expires_at) <= utc_now():
            raise BankError('确认卡已过期，请重新生成', 'BILL_ACTION_EXPIRED', 409)
        payload = unpack(row)
        arguments = PrepareBillChange.model_validate_json(json.dumps(payload['arguments']))
        result = await apply(db, user_id, arguments.change)
        payload['result'] = jsonable_encoder(result)
        row.payload_ciphertext = conversations._encrypt(json.dumps(payload, ensure_ascii=False))
        row.status = 'completed'
    await db.commit()
    return public(row)
