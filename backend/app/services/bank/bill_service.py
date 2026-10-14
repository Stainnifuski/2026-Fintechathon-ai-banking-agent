"""Bank-owned, authenticated billing reads; no ledger mutations or model calls."""
import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, not_, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import AccountORM
from ...models.transaction import TransactionORM as Tx
from ...schemas.bills import BillQuery, Totals
from .transfer_service import BankError

TZ = ZoneInfo("Asia/Shanghai")
CONSUMPTION = ("purchase", "subscription")
INCOMING = ("salary", "transfer_in", "refund", "investment_redemption")
OUTGOING = (*CONSUMPTION, "transfer_out", "investment_purchase")
LABELS = {"dining": "餐饮", "shopping": "购物", "transport": "交通",
          "entertainment": "娱乐", "subscription": "订阅", "other": "其他"}


@dataclass(frozen=True)
class AnomalyRules:
    large_fen: int = 100_000
    relative_min_fen: int = 10_000
    median_multiple: int = 3
    min_samples: int = 5
    duplicate_seconds: int = 300
    increase_min_fen: int = 20_000
    increase_percent: int = 50


RULES = AnomalyRules()


def aware(value: datetime) -> datetime:
    # Existing SQLAlchemy/SQLite records lose tzinfo. The storage contract is UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def percent(current: int, previous: int) -> float | None:
    return round((current - previous) * 100 / previous, 2) if previous else None


def money(fen: int) -> str:
    return f"¥{Decimal(fen) / 100:,.2f}"


def shifted(value: datetime, *, months: int = 0, years: int = 0) -> datetime:
    index = value.year * 12 + value.month - 1 + months + years * 12
    year, month = divmod(index, 12)
    month += 1
    return value.replace(year=year, month=month,
                         day=min(value.day, calendar.monthrange(year, month)[1]))


def bounds(query: BillQuery, now: datetime | None = None) -> dict:
    now = aware(now or datetime.now(timezone.utc)).astimezone(TZ)
    date = query.date or now.strftime("%Y-%m" if query.period == "month" else "%Y")
    pattern = r"\d{4}-\d{2}" if query.period == "month" else r"\d{4}"
    try:
        if not re.fullmatch(pattern, date):
            raise ValueError()
        start = datetime.strptime(date, "%Y-%m" if query.period == "month" else "%Y").replace(tzinfo=TZ)
        if not 2000 <= start.year <= 9998 or start > now:
            raise ValueError()
    except ValueError as error:
        raise BankError("请选择有效且不晚于当前时间的月份或年份（2000年起）", "INVALID_BILL_PERIOD", 422) from error
    shift = {"months": 1} if query.period == "month" else {"years": 1}
    back = {k: -v for k, v in shift.items()}
    natural_end = shifted(start, **shift)
    end = min(now, natural_end)
    partial = now < natural_end
    previous_start = shifted(start, **back)
    previous_end = shifted(end, **back) if partial else start
    return dict(query=query.model_copy(update={"date": date}).model_dump(), start=start,
                end_exclusive=end, previous_start=previous_start,
                previous_end_exclusive=previous_end, as_of=now, partial=partial)


async def scope(db: AsyncSession, user_id: str, query: BillQuery):
    from ...models.wealth_research import WealthContextORM
    accounts = list((await db.scalars(select(AccountORM.account_id).where(
        AccountORM.user_id == user_id, AccountORM.account_id.not_in(select(WealthContextORM.account_id))))).all())
    if query.account_id and query.account_id not in accounts:
        raise BankError("账户不存在或无权访问", "ACCOUNT_NOT_FOUND", 404)
    predicates = [Tx.account_id.in_([query.account_id] if query.account_id else accounts), Tx.status == "completed"]
    if not query.account_id:
        predicates.append(not_(and_(Tx.type.in_(("transfer_in", "transfer_out")), Tx.counterparty.in_(accounts))))
    # Category filters deliberately select consumption only; refunds lack an original-tx link.
    if query.category:
        predicates.extend([Tx.type.in_(CONSUMPTION), category_expr() == query.category])
    return predicates


def category_expr():
    return case((Tx.type == "subscription", "subscription"),
                (Tx.category.in_(tuple(LABELS)), Tx.category), else_="other")


def interval(predicates, start, end):
    return [*predicates, Tx.timestamp >= start.astimezone(timezone.utc),
            Tx.timestamp < end.astimezone(timezone.utc)]


async def aggregate(db, predicates, start, end, period):
    # Database aggregation covers every matching row, independent of detail pagination.
    if db.bind.dialect.name == "sqlite":
        day = func.strftime("%Y-%m-%d" if period == "month" else "%Y-%m", Tx.timestamp, "+8 hours")
    else:
        day = func.to_char(func.timezone("Asia/Shanghai", Tx.timestamp), "YYYY-MM-DD" if period == "month" else "YYYY-MM")
    category = category_expr()
    rows = (await db.execute(select(Tx.type, category, day, func.sum(Tx.amount_fen), func.count())
                            .where(*interval(predicates, start, end))
                            .group_by(Tx.type, category, day))).all()
    totals = Totals().model_dump()
    categories = defaultdict(lambda: [0, 0])
    trend = defaultdict(int)
    for kind, cat, day_key, amount, count in rows:
        amount = int(amount)
        totals["count"] += count
        if kind in INCOMING:
            totals["inflow_fen"] += amount
        elif kind in OUTGOING:
            totals["outflow_fen"] += amount
        else:
            totals["unknown_count"] += count
        if kind in CONSUMPTION:
            totals["consumption_fen"] += amount
            totals["consumption_count"] += count
            categories[cat][0] += amount
            categories[cat][1] += count
            trend[day_key] += amount
        elif kind in INCOMING or kind in ("transfer_out", "investment_purchase"):
            totals[kind + "_fen"] += amount
    totals["net_inflow_fen"] = totals["inflow_fen"] - totals["outflow_fen"]
    totals["net_consumption_fen"] = totals["consumption_fen"] - totals["refund_fen"]
    return totals, categories, trend


def transaction_view(tx):
    return dict(tx_id=tx.tx_id, account_id=tx.account_id, type=tx.type,
                category="subscription" if tx.type == "subscription" else tx.category,
                amount_fen=tx.amount_fen, timestamp=aware(tx.timestamp), counterparty=tx.counterparty,
                note=tx.note, status=tx.status)


async def analyze(db: AsyncSession, user_id: str, query: BillQuery, *, now=None, rules=RULES) -> dict:
    b = bounds(query, now)
    predicates = await scope(db, user_id, query)
    current, cats, trend = await aggregate(db, predicates, b["start"], b["end_exclusive"], query.period)
    previous, prev_cats, _ = await aggregate(db, predicates, b["previous_start"], b["previous_end_exclusive"], query.period)
    categories = [dict(category=key, label=LABELS[key], amount_fen=cats[key][0], count=cats[key][1],
                       share_percent=round(cats[key][0] * 100 / current["consumption_fen"], 2) if current["consumption_fen"] else 0,
                       previous_fen=prev_cats[key][0], change_fen=cats[key][0] - prev_cats[key][0],
                       change_percent=percent(cats[key][0], prev_cats[key][0]))
                  for key in sorted(set(cats) | set(prev_cats))]
    categories.sort(key=lambda c: (-c["amount_fen"], c["category"]))
    # Raw rows are used only for rule evidence, never as a capped source for totals.
    purchases = list((await db.scalars(select(Tx).where(*interval(predicates, b["start"], b["end_exclusive"]),
                                                       Tx.type.in_(CONSUMPTION)).order_by(Tx.timestamp, Tx.tx_id))).all())
    baseline = list((await db.scalars(select(Tx.amount_fen).where(
        *interval(predicates, b["previous_start"], b["previous_end_exclusive"]), Tx.type.in_(CONSUMPTION)))).all())
    typical = median([Decimal(value) for value in baseline]) if len(baseline) >= rules.min_samples else None
    anomalies = []
    recent = {}
    for tx in purchases:
        absolute = tx.amount_fen >= rules.large_fen
        relative = typical is not None and tx.amount_fen >= rules.relative_min_fen and tx.amount_fen >= typical * rules.median_multiple
        if absolute or relative:
            reason = f"本笔消费 {money(tx.amount_fen)}，达到大额提醒阈值 {money(rules.large_fen)}。" if absolute else f"本笔消费 {money(tx.amount_fen)}，达到上期 {len(baseline)} 笔消费中位数（约 {money(typical)}）的 {rules.median_multiple} 倍以上。"
            anomalies.append(dict(kind="large", title="大额消费，建议核对", reason=reason, tx_ids=[tx.tx_id]))
        key = (tx.account_id, tx.type, tx.counterparty, tx.amount_fen)
        earlier = recent.get(key)
        if earlier and (aware(tx.timestamp) - aware(earlier.timestamp)).total_seconds() <= rules.duplicate_seconds:
            seconds = int((aware(tx.timestamp) - aware(earlier.timestamp)).total_seconds())
            anomalies.append(dict(kind="duplicate", title="疑似重复扣费", reason=f"同账户、同商户、同类型的两笔 {money(tx.amount_fen)} 消费相隔 {seconds} 秒；可能是正常多次购买，请核对。", tx_ids=[earlier.tx_id, tx.tx_id]))
        recent[key] = tx
    for cat in categories:
        if cat["previous_fen"] and cat["change_fen"] >= rules.increase_min_fen and cat["change_fen"] * 100 >= cat["previous_fen"] * rules.increase_percent:
            anomalies.append(dict(kind="increase", title=f"{cat['label']}消费增加", category=cat["category"],
                                  reason=f"本期 {money(cat['amount_fen'])}，上期 {money(cat['previous_fen'])}，增加 {money(cat['change_fen'])}（{cat['change_percent']}%）。"))
    points = []
    cursor = b["start"]
    while cursor < b["end_exclusive"]:
        key = cursor.strftime("%Y-%m-%d" if query.period == "month" else "%Y-%m")
        points.append(dict(date=key, consumption_fen=trend[key]))
        cursor = cursor + timedelta(days=1) if query.period == "month" else shifted(cursor, months=1)
    notices = ["仅统计已完成交易；消费含购买和订阅，转账单列。",
               "退款按到账期单列；未关联原消费，因此不抵扣分类金额。",
               "全部账户汇总排除可确认的本人账户互转；单账户保留转入转出。",
               "所有金额为人民币；历史无时区时间按 UTC 解释，北京时间划分账期。"]
    if typical is None:
        notices.append(f"上期消费样本不足 {rules.min_samples} 笔，未启用个人历史偏离判断；仍检查固定大额和疑似重复扣费。")
    if current["unknown_count"]:
        notices.append(f"存在 {current['unknown_count']} 笔未知交易类型，仅计笔数，未计入金额。")
    change = current["consumption_fen"] - previous["consumption_fen"]
    change_pct = percent(current["consumption_fen"], previous["consumption_fen"])
    report = [f"{b['query']['date']} {'月度' if query.period == 'month' else '年度'}账单：已完成 {current['count']} 笔，消费原额 {money(current['consumption_fen'])}，退款 {money(current['refund_fen'])}，净消费 {money(current['net_consumption_fen'])}。",
              f"资金流入 {money(current['inflow_fen'])}，流出 {money(current['outflow_fen'])}，净流入 {money(current['net_inflow_fen'])}。"]
    comparison = "上期同期" if b["partial"] else "完整上期"
    report.append(f"与{comparison}相比，消费变化 {money(change)}" + (f"（{change_pct:+.2f}%）。" if change_pct is not None else "；上期消费为零，不计算增长率。"))
    if current["consumption_fen"]:
        top = categories[0]
        report.append(f"消费最多的类别是{top['label']}，共 {money(top['amount_fen'])}，占消费原额的 {top['share_percent']}%。")
    increased = sorted((c for c in categories if c["change_fen"] > 0), key=lambda c: c["change_fen"], reverse=True)
    if increased:
        top = increased[0]
        report.append(f"增加最多的类别是{top['label']}，比上期多 {money(top['change_fen'])}；这说明支出构成变化，不代表已知购买原因。")
    report.append(f"发现 {len(anomalies)} 条待核对提示，具体依据见下方明细。" if anomalies else "本期未触发已配置的异常规则；这不代表所有交易均无异常。")
    from .bill_explanation import explain_changes
    explanation = await explain_changes(db, predicates, b)
    if explanation['total_change_fen'] != change:
        raise BankError('账单在分析期间发生变化，请重新查询', 'BILL_CHANGED', 409)
    if explanation['drivers']:
        driver = explanation['drivers'][0]
        report.append(f"按商户追溯：{driver['merchant']}（{LABELS[driver['category']]}）变化 {money(driver['change_fen'])}；{driver['explanation']}")
    return dict(**b, change_explanation=explanation, totals=current, previous_totals=previous, change_fen=change, change_percent=change_pct,
                categories=categories, trend=points, anomalies=anomalies, report=report, notices=notices)


async def transactions(db, user_id, query, *, page=1, page_size=20, tx_ids=None, as_of=None):
    if as_of and (as_of.tzinfo is None or as_of > datetime.now(timezone.utc)):
        raise BankError("数据截止时间必须带时区且不能晚于当前时间", "INVALID_AS_OF", 422)
    b = bounds(query, as_of)
    predicates = interval(await scope(db, user_id, query), b["start"], b["end_exclusive"])
    if tx_ids:
        predicates.append(Tx.tx_id.in_(tx_ids))
    total = await db.scalar(select(func.count()).select_from(Tx).where(*predicates))
    rows = (await db.scalars(select(Tx).where(*predicates).order_by(Tx.timestamp.desc(), Tx.tx_id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).all()
    return dict(items=[transaction_view(tx) for tx in rows], total=total, page=page, page_size=page_size)
