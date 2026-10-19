"""Read-only projection: plans + evidence-backed estimates + explicit user scenarios."""
from .account_scope import isolated_account_ids

import calendar
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

from sqlalchemy import select
from ...models.account import AccountORM
from ...models.subscription import SubscriptionORM
from ...models.wealth_research import WealthContextORM
from ...models.workflow import TransferTaskORM, TransferPlanORM, TransferOperationORM
from ...schemas.bill_insights import CashflowScenario
from .available_funds import held_cash
from .bill_service import Tx, TZ, aware, shifted, LABELS
from .bill_explanation import rounded
from .transfer_service import BankError


def recurrence(anchor, month_offset):
    # Shift the ORIGINAL anchor: a February clamp must not permanently move a 31st payday.
    return shifted(anchor, months=month_offset)


async def project_cashflow(db, user_id, request: CashflowScenario, *, now=None):
    now = aware(now or datetime.now(timezone.utc)).astimezone(TZ)
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(days=request.days)
    all_accounts = list((await db.scalars(select(AccountORM).where(
        AccountORM.user_id == user_id, AccountORM.account_id.not_in(isolated_account_ids())))).all())
    owned = {a.account_id: a for a in all_accounts}
    if request.account_id and request.account_id not in owned:
        raise BankError('账户不存在或无权访问', 'ACCOUNT_NOT_FOUND', 404)
    if request.account_id and (owned[request.account_id].account_type != 'savings' or owned[request.account_id].status != 'active'):
        raise BankError('现金流仅支持有效储蓄账户，信用额度不计现金', 'CASH_ACCOUNT_REQUIRED', 422)
    accounts = [a for a in all_accounts if a.status == 'active' and a.account_type == 'savings'
                and (not request.account_id or a.account_id == request.account_id)]
    ids = {a.account_id for a in accounts}
    categories = [r.category for r in request.reductions]
    if len(set(categories)) != len(categories) or len(set(request.excluded_subscription_ids)) != len(request.excluded_subscription_ids):
        raise BankError('情景参数不能包含重复类别或订阅', 'DUPLICATE_SCENARIO_PARAMETER', 422)
    reductions = {r.category: r.percent for r in request.reductions}
    opening = sum(a.balance_fen for a in accounts)
    held = sum([await held_cash(db, a.account_id) for a in accounts])
    available = opening - held
    notices = ['预测从当前可用储蓄现金开始，已扣除理财占款；信用额度和测试上下文账户不纳入。',
               '计划收支按当前记录推演，不代表一定执行；估计收入和消费不是银行事实。',
               '减少消费、排除订阅都是本次计算假设，不会取消订阅、修改预算或执行资金操作。',
               '低／高余额线只表示最近完整月份支出高低情景，不是统计置信区间。',
               '消费基线使用最近3个完整日历月的已记录流水，无流水日期按0折算；未接入的外部账单不包含。',
               '不预测投资收益、未知付款、退款或未来新增业务；预测覆盖从当前时刻起的完整天数，首尾日期按实际时长折算消费。']
    if not ids:
        notices.append('没有可纳入的有效储蓄现金账户；以下仅为空范围计算。')
    events = []
    for item in request.one_off_events:
        if item.at.tzinfo is None or not now<=item.at<end or item.amount_fen==0 or not item.label.strip():
            raise BankError('临时收支需带时区、金额非零且位于预测范围内','INVALID_SCENARIO_EVENT',422)
        events.append(dict(at=item.at.astimezone(TZ),label=item.label,amount_fen=item.amount_fen,source='scenario',reference_id=None,evidence_tx_ids=[],excluded_in_scenario=False))

    subs = list((await db.scalars(select(SubscriptionORM).where(SubscriptionORM.user_id == user_id,
                                                                SubscriptionORM.debit_account_id.in_(ids),
                                                                SubscriptionORM.status == 'ACTIVE'))).all())
    valid_subs = {s.subscription_id: s for s in subs if s.currency == 'CNY' and s.billing_cycle == 'MONTHLY' and s.amount_fen > 0}
    if len(valid_subs) != len(subs):
        notices.append(f'{len(subs) - len(valid_subs)}条订阅不是有效人民币月度计划，未推演其扣费。')
    if any(key not in valid_subs for key in request.excluded_subscription_ids):
        raise BankError('情景订阅不存在、已取消或不在当前现金账户范围', 'SCENARIO_SUBSCRIPTION_NOT_FOUND', 404)
    for sub in valid_subs.values():
        anchor = aware(sub.next_charge_at).astimezone(TZ)
        if anchor < now:
            notices.append(f'{sub.merchant_name} 的计划扣费日期已过期，未估算补扣；后续按月度计划推演。')
        offset = max(0, (now.year - anchor.year) * 12 + now.month - anchor.month)
        for k in range(offset, offset + 4):
            when = recurrence(anchor, k)
            if now <= when < end:
                events.append(dict(at=when, label=f'{sub.merchant_name} 计划扣费', amount_fen=-sub.amount_fen,
                                   source='subscription_plan', reference_id=sub.subscription_id, evidence_tx_ids=[],
                                   excluded_in_scenario=sub.subscription_id in request.excluded_subscription_ids))
    # Only authorized waiting tasks and their CURRENT ready operation qualify as plans.
    scheduled = (await db.execute(select(TransferTaskORM, TransferPlanORM, TransferOperationORM)
        .join(TransferPlanORM, TransferPlanORM.task_id == TransferTaskORM.task_id)
        .join(TransferOperationORM, TransferOperationORM.task_id == TransferTaskORM.task_id)
        .where(TransferTaskORM.user_id == user_id, TransferTaskORM.phase == 'WAITING_EXECUTION',
               TransferTaskORM.cancel_requested.is_(False), TransferPlanORM.is_current.is_(True),
               TransferPlanORM.plan_version == TransferTaskORM.current_plan_version,
               TransferOperationORM.plan_version == TransferTaskORM.current_plan_version,
               TransferOperationORM.status == 'READY', TransferOperationORM.currency == 'CNY'))).all()
    for task, plan, operation in scheduled:
        try:
            when = datetime.fromisoformat(plan.params.get('scheduled_at', ''))
            if when.tzinfo is None:
                continue
        except (ValueError, TypeError):
            continue
        if when < now:
            notices.append('存在已到期但尚未执行的定时转账，未将其当作已付款或估算补执行。')
            continue
        if not when < end or operation.amount_fen <= 0:
            continue
        delta = operation.amount_fen * (int(operation.to_account in ids) - int(operation.from_account in ids))
        if delta:
            events.append(dict(at=when.astimezone(TZ), label='已确认定时转账计划', amount_fen=delta,
                               source='scheduled_transfer', reference_id=task.task_id, evidence_tx_ids=[], excluded_in_scenario=False))
    month_start = start_day.replace(day=1)
    history_start = shifted(month_start, months=-3)
    history = (await db.scalars(select(Tx).where(Tx.account_id.in_(ids), Tx.status == 'completed',
                                                Tx.timestamp >= history_start.astimezone(timezone.utc),
                                                Tx.timestamp < now.astimezone(timezone.utc),
                                                Tx.type.in_(('purchase', 'salary'))).order_by(Tx.timestamp))).all()
    from ...models.bill_foundation import BillRecurringStreamORM as Stream, BillRecurringOccurrenceORM as Occurrence, BillMerchantORM as Merchant, BillTransactionMetaORM as Meta
    streams = (await db.execute(select(Stream, Merchant.display_name).join(Merchant, Merchant.merchant_id == Stream.merchant_id).where(
        Stream.user_id == user_id, Stream.account_id.in_(ids), Stream.source == 'detected', Stream.status == 'ACTIVE'))).all()
    observed = defaultdict(list)
    for sid, tx in (await db.execute(select(Occurrence.stream_id, Tx).join(Tx, Tx.tx_id == Occurrence.tx_id).where(
        Occurrence.stream_id.in_([stream.stream_id for stream, _ in streams]), Tx.account_id.in_(ids)))).all():
        observed[sid].append(tx)
    history_metadata = {m.tx_id:m for m in (await db.scalars(select(Meta).where(Meta.tx_id.in_([tx.tx_id for tx in history])))).all()}
    fixed_expense_ids = set()
    salary_count = 0
    from ...models.bill_features import BillRecurringScheduleORM as Schedule
    from .bill_schedules import occurrences
    schedules={r.stream_id:r for r in (await db.scalars(select(Schedule).where(Schedule.stream_id.in_([s.stream_id for s,_ in streams])))).all()}
    def period_key(at,frequency):
        at=aware(at).astimezone(TZ)
        if frequency=='WEEKLY':return at.isocalendar()[:2]
        if frequency=='QUARTERLY':return at.year,(at.month-1)//3
        if frequency=='YEARLY':return at.year
        return at.year,at.month
    for stream, merchant in streams:
        if not stream.enabled or not stream.last_observed_at:continue
        last=aware(stream.last_observed_at).astimezone(TZ)
        max_gap={'WEEKLY':10,'MONTHLY':62,'QUARTERLY':124,'YEARLY':397}.get(stream.frequency,62)
        if (now-last).days>max_gap:
            notices.append(f'{merchant} 周期记录已过期，未继续推演，请刷新识别。');continue
        rows=observed[stream.stream_id]
        if stream.kind=='expense':fixed_expense_ids.update(t.tx_id for t in rows)
        schedule=schedules.get(stream.stream_id)
        anchor=aware(schedule.anchor_at).astimezone(TZ) if schedule else month_start.replace(day=min(stream.anchor_day,calendar.monthrange(month_start.year,month_start.month)[1]),hour=12)
        for when in occurrences(anchor,stream.frequency,now,end,month_end=stream.month_end):
            already=any(period_key(t.timestamp,stream.frequency)==period_key(when,stream.frequency) for t in rows)
            if already:continue
            income=stream.kind=='income'
            events.append(dict(at=when,label=f'{merchant} 周期{ "收入" if income else "支出" }估计',amount_fen=stream.amount_fen if income else -stream.amount_fen,
                source='salary_estimate' if income else 'recurring_estimate',reference_id=stream.stream_id,evidence_tx_ids=[t.tx_id for t in sorted(rows,key=lambda t:aware(t.timestamp))[-3:]],excluded_in_scenario=False))
            if income:salary_count+=1
    from ...models.bill_features import BillRecurringScheduleORM as Schedule
    from .bill_schedules import occurrences
    manual=(await db.execute(select(Stream,Schedule,Merchant.display_name).join(Schedule,Schedule.stream_id==Stream.stream_id).join(Merchant,Merchant.merchant_id==Stream.merchant_id).where(Stream.user_id==user_id,Stream.account_id.in_(ids),Stream.source=='manual',Stream.status=='ACTIVE',Stream.enabled.is_(True)))).all()
    for stream,schedule,merchant in manual:
        if stream.kind=='expense':
            fixed_expense_ids.update(tx.tx_id for tx in history if tx.type=='purchase' and tx.account_id==stream.account_id and history_metadata.get(tx.tx_id) and history_metadata[tx.tx_id].merchant_id==stream.merchant_id and tx.amount_fen==stream.amount_fen)
        for at in occurrences(schedule.anchor_at,stream.frequency,now,end,month_end=stream.month_end):
            events.append(dict(at=at,label=f'{merchant} 手动周期计划',amount_fen=stream.amount_fen if stream.kind=='income' else -stream.amount_fen,source='recurring_plan',reference_id=stream.stream_id,evidence_tx_ids=[],excluded_in_scenario=False))
    if fixed_expense_ids:
        notices.append('已启用周期支出的历史记录从可变消费基线移除，避免重复计算；消费降幅只作用于可变消费。')
    # Compatibility for old, not-yet-backfilled records. Persisted income preferences take priority.
    # Salary estimates require one payment per month, at least 3 months, stable date and amount.
    salaries = defaultdict(list)
    for tx in history:
        meta = history_metadata.get(tx.tx_id)
        if tx.type == 'salary' and (not meta or not meta.merchant_id):
            salaries[(tx.account_id, tx.counterparty)].append(tx)
    for (_, merchant), rows in salaries.items():
        by_month = defaultdict(list)
        for tx in rows:
            by_month[aware(tx.timestamp).astimezone(TZ).strftime('%Y-%m')].append(tx)
        sample = [batch[0] for batch in by_month.values()]
        days = [aware(t.timestamp).astimezone(TZ).day for t in sample]
        amounts = [t.amount_fen for t in sample]
        if len(sample) < 3 or any(len(batch) != 1 for batch in by_month.values()):
            continue
        # Month-end salaries are stable even when months have different day counts.
        at_month_end = all(aware(t.timestamp).astimezone(TZ).day == calendar.monthrange(
            aware(t.timestamp).year, aware(t.timestamp).month)[1] for t in sample)
        amount = rounded(median([Decimal(v) for v in amounts]))
        if (max(days) - min(days) > 3 and not at_month_end) or max(amounts) - min(amounts) > amount // 5:
            continue
        # Construct each target month independently so month-end stays at month-end.
        for k in range(4):
            target = shifted(month_start, months=k)
            target_day = calendar.monthrange(target.year, target.month)[1] if at_month_end else min(int(median(days)), calendar.monthrange(target.year, target.month)[1])
            when = target.replace(day=target_day, hour=12)
            already = any(aware(t.timestamp).astimezone(TZ).strftime('%Y-%m') == when.strftime('%Y-%m') for t in rows)
            if now <= when < end and not already:
                events.append(dict(at=when, label=f'{merchant} 历史工资估计', amount_fen=amount,
                                   source='salary_estimate', reference_id=None,
                                   evidence_tx_ids=[t.tx_id for t in sample[-3:]], excluded_in_scenario=False))
                salary_count += 1
    if not salary_count:
        notices.append('未找到符合3个月稳定记录条件的未来工资收入，未补造收入。')
    months = [shifted(month_start, months=-k) for k in (3, 2, 1)]
    monthly = defaultdict(lambda: defaultdict(int))
    samples = defaultdict(list)
    from .bill_classification import classification_views
    effective_categories=await classification_views(db,user_id,[tx.tx_id for tx in history])
    for tx in history:
        when = aware(tx.timestamp).astimezone(TZ)
        if tx.type != 'purchase' or when >= month_start or tx.tx_id in fixed_expense_ids:
            continue
        cat = effective_categories.get(tx.tx_id,{}).get('effective_category',tx.category)
        cat = cat if cat in LABELS else 'other'
        monthly[cat][when.strftime('%Y-%m')] += tx.amount_fen
        samples[cat].append(tx)
    rates, low_rates, high_rates = {}, {}, {}
    baselines = []
    observation_days = (month_start - history_start).days
    for cat, rows in samples.items():
        observed = len({aware(t.timestamp).astimezone(TZ).strftime('%Y-%m') for t in rows})
        if len(rows) < 5 or observed < 2:
            notices.append(f'{LABELS[cat]}历史不足5笔或2个完整月份，未估计该类未来消费。')
            continue
        rate = Decimal(sum(t.amount_fen for t in rows)) / observation_days
        month_rates = [Decimal(monthly[cat][m.strftime('%Y-%m')]) / calendar.monthrange(m.year, m.month)[1] for m in months]
        rates[cat], low_rates[cat], high_rates[cat] = rate, min(month_rates), max(month_rates)
        baselines.append(dict(category=cat, daily_fen=rounded(rate), sample_count=len(rows), observed_months=observed,
                              estimated_total_fen=0, scenario_total_fen=0))
    from .bill_forecasts import calibration
    calibration_info=await calibration(db,user_id,request.account_id,now)
    factor=Decimal(str(calibration_info['factor']))
    rates={cat:rate*factor for cat,rate in rates.items()}
    low_rates={cat:rate*factor for cat,rate in low_rates.items()}
    high_rates={cat:rate*factor for cat,rate in high_rates.items()}
    for baseline in baselines:baseline['daily_fen']=rounded(rates[baseline['category']])
    if not rates:
        notices.append('消费历史不足，基准余额仅包含已识别计划与收入估计；不代表未来消费为零。')
    events.sort(key=lambda e: (e['at'], e['source'], e['reference_id'] or ''))
    timeline = []
    planned_balance = baseline_balance = scenario_balance = low_balance = high_balance = available
    planned_out = estimated_income = estimated_recurring = variable_total = 0
    first_negative = start_day.date().isoformat() if available < 0 else None
    previous_expected = {cat: 0 for cat in rates}
    previous_scenario = {cat: 0 for cat in rates}
    elapsed = Decimal(0)
    for index in range((end.date() - start_day.date()).days + 1):
        day = start_day + timedelta(days=index)
        next_day = day + timedelta(days=1)
        seconds = (min(next_day, end) - max(day, now)).total_seconds()
        if seconds <= 0:
            continue
        weight = Decimal(str(seconds)) / 86400
        elapsed += weight
        daily_variable = daily_scenario = low_spend = high_spend = 0
        for cat, rate in rates.items():
            cumulative = rounded(rate * elapsed)
            adjusted = rounded(rate * elapsed * (100 - reductions.get(cat, 0)) / 100)
            daily_variable += cumulative - previous_expected[cat]
            daily_scenario += adjusted - previous_scenario[cat]
            previous_expected[cat], previous_scenario[cat] = cumulative, adjusted
            low_spend += rounded(low_rates[cat] * elapsed) - rounded(low_rates[cat] * (elapsed - weight))
            high_spend += rounded(high_rates[cat] * elapsed) - rounded(high_rates[cat] * (elapsed - weight))
        for event in (e for e in events if day <= e['at'] < next_day):
            amount = event['amount_fen']
            if event['source']=='scenario':
                scenario_balance += amount
                continue
            baseline_balance += amount
            low_balance += amount
            high_balance += amount
            if not event['excluded_in_scenario']:
                scenario_balance += amount
            if event['source'] == 'salary_estimate':
                estimated_income += amount
            elif event['source'] == 'recurring_estimate':
                estimated_recurring -= amount
            else:
                planned_balance += amount
                if amount < 0:
                    planned_out -= amount
        baseline_balance -= daily_variable
        scenario_balance -= daily_scenario
        low_balance -= high_spend
        high_balance -= low_spend
        variable_total += daily_variable
        key = day.date().isoformat()
        if baseline_balance < 0 and first_negative is None:
            first_negative = key
        timeline.append(dict(date=key, planned_balance_fen=planned_balance, baseline_balance_fen=baseline_balance,
                             scenario_balance_fen=scenario_balance, historical_low_balance_fen=low_balance,
                             historical_high_balance_fen=high_balance))
    for baseline in baselines:
        cat = baseline['category']
        baseline['estimated_total_fen'] = previous_expected[cat]
        baseline['scenario_total_fen'] = previous_scenario[cat]
    return dict(calibration=calibration_info, as_of=now, end_exclusive=end, account_id=request.account_id, days=request.days,
                opening_balance_fen=opening, held_fen=held, opening_available_fen=available,
                planned_outflow_fen=planned_out, estimated_income_fen=estimated_income, estimated_recurring_spending_fen=estimated_recurring,
                estimated_variable_spending_fen=variable_total, baseline_end_fen=baseline_balance,
                scenario_end_fen=scenario_balance, scenario_saving_fen=scenario_balance - baseline_balance,
                minimum_baseline_fen=min([available] + [p['baseline_balance_fen'] for p in timeline]),
                first_negative_date=first_negative, scenario=request.model_dump(), spending_baselines=baselines,
                events=events, timeline=timeline, notices=notices)
