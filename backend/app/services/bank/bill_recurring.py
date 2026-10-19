"""Persisted monthly streams, with explicit observations and forecast preferences."""
import calendar
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select
from ...models.subscription import SubscriptionORM
from ...models.bill_foundation import BillRecurringStreamORM as Stream, BillRecurringOccurrenceORM as Occurrence, BillMerchantORM as Merchant, BillMetadataEventORM as Event
from .bill_foundation import key, utc, merchant_registry, lock_users, account_ids
from .transfer_service import BankError

TZ = ZoneInfo('Asia/Shanghai')


def month_index(at):
    return at.year * 12 + at.month


def next_date(now, day, month_end):
    for offset in range(3):
        year, month = divmod(now.year * 12 + now.month - 1 + offset, 12)
        month += 1
        last = calendar.monthrange(year, month)[1]
        at = now.replace(year=year, month=month, day=last if month_end else min(day, last), hour=12, minute=0, second=0, microsecond=0)
        if at >= now:
            return at


async def save_stream(db, stream_id, values):
    values = {field: utc(value) if isinstance(value, datetime) else value for field, value in values.items()}
    stream = await db.get(Stream, stream_id)
    if stream is None:
        stream = Stream(stream_id=stream_id, version=1, **values)
        db.add(stream)
    else:
        # Preferences survive observation refresh; official subscription status remains bank-owned.
        values.pop('enabled', None)
        changed = False
        for field, value in values.items():
            old = getattr(stream, field)
            same = utc(old) == utc(value) if isinstance(old, datetime) and isinstance(value, datetime) else old == value
            if not same:
                setattr(stream, field, value)
                changed = True
        if changed:
            stream.version += 1
    await db.flush()
    return stream


def detect_pattern(rows,now):
    for frequency,months,step in [('MONTHLY',3,1),('WEEKLY',2,0),('QUARTERLY',12,3),('YEARLY',36,12)]:
        sample=sorted([t for t in rows if month_index(utc(t.timestamp).astimezone(TZ))>=month_index(now)-months],key=lambda t:utc(t.timestamp))
        if len(sample)<3:continue
        dates=[utc(t.timestamp).astimezone(TZ) for t in sample]
        amounts=[t.amount_fen for t in sample]
        amount=int(Decimal(median([Decimal(v) for v in amounts])).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        if amount<=0 or max(amounts)-min(amounts)>amount//5:continue
        days=[d.day for d in dates]
        month_end=all(d.day==calendar.monthrange(d.year,d.month)[1] for d in dates)
        if frequency=='WEEKLY':
            gaps=[(right-left).total_seconds()/86400 for left,right in zip(dates,dates[1:])]
            if not all(6<=gap<=8 for gap in gaps) or (now-dates[-1]).days>9:continue
            return frequency,sample,amount,dates[-1].day,False
        indexes=[month_index(d) for d in dates]
        if any(right-left!=step for left,right in zip(indexes,indexes[1:])) or month_index(now)-indexes[-1]>step:continue
        if max(days)-min(days)>3 and not month_end:continue
        return frequency,sample,amount,int(median(days)),month_end
    return None


async def sync_recurring(db, user_id, ids, transactions, metas, *, now=None, touched=None):
    now = utc(now or datetime.now(timezone.utc)).astimezone(TZ)
    subs = list((await db.scalars(select(SubscriptionORM).where(SubscriptionORM.user_id == user_id, SubscriptionORM.debit_account_id.in_(ids)))).all())
    sub_keys = defaultdict(list)
    seen = set()
    for sub in subs:
        if sub.billing_cycle != 'MONTHLY' or sub.currency != 'CNY' or sub.amount_fen <= 0:
            continue
        merchant = await merchant_registry(db, user_id, sub.merchant_name)
        if merchant is None:
            continue
        sub_keys[(sub.debit_account_id, merchant.merchant_id)].append(sub)
        anchor = utc(sub.next_charge_at).astimezone(TZ)
        stream = await save_stream(db, key('R-', 'sub:'+sub.subscription_id), dict(
            user_id=user_id, account_id=sub.debit_account_id, merchant_id=merchant.merchant_id, kind='expense', source='subscription',
            subscription_id=sub.subscription_id, anchor_day=anchor.day, month_end=False, amount_fen=sub.amount_fen,
            status='ACTIVE' if sub.status == 'ACTIVE' else 'CANCELLED', enabled=True, next_expected_at=anchor))
        seen.add(stream.stream_id)
        # Same merchant may have multiple plans: do not invent a plan-to-charge relation.
    observations = defaultdict(list)
    cutoff = month_index(now) - 36
    for tx in transactions:
        meta = metas[tx.tx_id]
        if tx.status != 'completed' or not meta.merchant_id:
            continue
        plans = sub_keys.get((tx.account_id, meta.merchant_id), []) if tx.type == 'subscription' else []
        if len(plans) == 1:
            observations[key('R-', 'sub:'+plans[0].subscription_id)].append(tx)
        elif tx.type in ('salary', 'purchase') and cutoff <= month_index(utc(tx.timestamp).astimezone(TZ)) <= month_index(now) and utc(tx.timestamp) <= now:
            observations[(tx.account_id, meta.merchant_id, 'income' if tx.type == 'salary' else 'expense')].append(tx)
    for group, rows in observations.items():
        if isinstance(group, str):
            stream = await db.get(Stream, group)
        else:
            account, merchant, kind = group
            # Do not duplicate existing bank subscription plans under purchase-derived candidates.
            if kind == 'expense' and (account, merchant) in sub_keys:
                continue
            detected=detect_pattern(rows,now)
            if not detected:continue
            frequency,sample,amount,anchor_day,end_of_month=detected
            from .bill_schedules import occurrences
            first=utc(min(sample,key=lambda t:utc(t.timestamp)).timestamp).astimezone(TZ)
            if frequency!='WEEKLY':first=first.replace(day=min(anchor_day,calendar.monthrange(first.year,first.month)[1]),hour=12,minute=0,second=0,microsecond=0)
            next_at=next(occurrences(first,frequency,now,now+timedelta(days=370),month_end=end_of_month),None)
            stream = await save_stream(db, key('R-', f'{user_id}:{account}:{merchant}:{kind}'), dict(
                user_id=user_id, account_id=account, merchant_id=merchant, kind=kind, source='detected', subscription_id=None,
                frequency=frequency,anchor_day=anchor_day, month_end=end_of_month, amount_fen=amount, status='ACTIVE', enabled=kind=='income',
                next_expected_at=next_at))
            from ...models.bill_features import BillRecurringScheduleORM as Schedule
            schedule=await db.get(Schedule,stream.stream_id)
            category=sample[-1].category if sample[-1].category in ('dining','shopping','transport','entertainment','subscription','education','other') else 'other'
            if schedule:schedule.anchor_at=utc(first);schedule.category=category
            else:db.add(Schedule(stream_id=stream.stream_id,anchor_at=utc(first),category=category))
            seen.add(stream.stream_id)
            rows = sample
        # Keep an observation history; future refreshes do not erase historical evidence.
        existing = set((await db.scalars(select(Occurrence.tx_id).where(Occurrence.stream_id == stream.stream_id))).all())
        for tx in rows:
            if tx.tx_id not in existing:
                db.add(Occurrence(stream_id=stream.stream_id, tx_id=tx.tx_id))
            metas[tx.tx_id].recurring_stream_id = stream.stream_id
        await db.flush()
        count = len(existing | {tx.tx_id for tx in rows})
        last = max((utc(tx.timestamp) for tx in rows), default=None)
        if stream.sample_count != count or (last and (stream.last_observed_at is None or utc(stream.last_observed_at) != last)):
            stream.sample_count, stream.last_observed_at = count, last
            stream.version += 1
    for stream in (await db.scalars(select(Stream).where(Stream.user_id == user_id, Stream.source != 'manual', Stream.status == 'ACTIVE'))).all():
        affected=touched is None or (stream.account_id,stream.merchant_id) in touched
        expired=stream.source=='detected' and stream.last_observed_at and month_index(now)-month_index(utc(stream.last_observed_at).astimezone(TZ))>({'MONTHLY':1,'QUARTERLY':3,'YEARLY':12,'WEEKLY':1}.get(stream.frequency,1))
        if (affected and stream.stream_id not in seen) or expired:
            stream.status = 'STALE'
            stream.version += 1
    await db.flush()


async def list_recurring(db, user_id):
    ids = await account_ids(db, user_id)
    rows = (await db.execute(select(Stream, Merchant.display_name).join(Merchant, Merchant.merchant_id == Stream.merchant_id).where(
        Stream.user_id == user_id, Stream.account_id.in_(ids)).order_by(Stream.status, Stream.kind, Merchant.display_name))).all()
    evidence = defaultdict(list)
    for stream_id, tx_id in (await db.execute(select(Occurrence.stream_id, Occurrence.tx_id).where(Occurrence.stream_id.in_([s.stream_id for s, _ in rows])))).all():
        evidence[stream_id].append(tx_id)
    from ...models.bill_features import BillRecurringScheduleORM as Schedule
    schedules={s.stream_id:s for s in (await db.scalars(select(Schedule).where(Schedule.stream_id.in_([s.stream_id for s,_ in rows])))).all()}
    return {'items': [dict(anchor_at=utc(schedules[s.stream_id].anchor_at) if s.stream_id in schedules else None, category=schedules[s.stream_id].category if s.stream_id in schedules else 'other', stream_id=s.stream_id, account_id=s.account_id, merchant_id=s.merchant_id, merchant=name,
        kind=s.kind, source=s.source, subscription_id=s.subscription_id, frequency=s.frequency, anchor_day=s.anchor_day,
        month_end=s.month_end, amount_fen=s.amount_fen, status=s.status, enabled=s.enabled, version=s.version,
        sample_count=s.sample_count, last_observed_at=utc(s.last_observed_at) if s.last_observed_at else None, next_expected_at=utc(s.next_expected_at) if s.next_expected_at else None,
        evidence_tx_ids=evidence[s.stream_id]) for s, name in rows],
        'notices': ['周期候选至少3笔：每周间隔6–8天，月／季度／年度按连续日历周期识别，日期差不超过3天或均为月末，金额跨度不超过中位数20%。',
                    '历史工资估计默认纳入；识别出的周期支出须手动启用，停用仅改变预测，不停止真实扣费。',
                    '可手动保存每周、每月、季度和年度计划；手动计划不会执行扣款。',
                    '订阅计划由银行订阅状态维护，取消扣费请走订阅取消确认流程。']}


async def set_enabled(db, user_id, stream_id, request):
    await lock_users(db, [user_id])
    stream = await db.get(Stream, stream_id, populate_existing=True)
    if not stream or stream.user_id != user_id or stream.account_id not in await account_ids(db, user_id):
        raise BankError('周期记录不存在或无权访问', 'RECURRING_NOT_FOUND', 404)
    if stream.source == 'subscription':
        raise BankError('订阅请使用银行的订阅取消确认流程', 'SUBSCRIPTION_CONFIRMATION_REQUIRED', 409)
    if stream.status != 'ACTIVE':
        raise BankError('该周期记录已失效，请重新识别', 'RECURRING_INACTIVE', 409)
    if request.enabled:
        duplicate=await db.scalar(select(Stream.stream_id).where(Stream.user_id==user_id,Stream.stream_id!=stream_id,Stream.account_id==stream.account_id,Stream.merchant_id==stream.merchant_id,Stream.kind==stream.kind,Stream.status=='ACTIVE',Stream.enabled.is_(True)))
        if duplicate:raise BankError('同账户同商户已有启用的周期计划，请先停用另一条','DUPLICATE_RECURRING_PLAN',409)
    if stream.enabled == request.enabled:
        return {'stream_id': stream_id, 'enabled': stream.enabled, 'version': stream.version}
    if stream.version != request.expected_version:
        raise BankError('周期记录已更新，请刷新后重试', 'STALE_RECURRING', 409)
    before = stream.enabled
    stream.enabled = request.enabled
    stream.version += 1
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='recurring_preference',subject_id=stream_id,
                 before={'enabled':before},after={'enabled':stream.enabled}))
    await db.flush()
    return {'stream_id':stream_id,'enabled':stream.enabled,'version':stream.version}
