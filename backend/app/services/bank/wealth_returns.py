"""Read-only actual settlement replay. Money is integer fen; shares are 1/10000.

Effective timestamps come from bank settlement evidence, never scheduled dates or
the remaining shares today. Missing evidence is returned as unknown, not repaired.
"""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext

from sqlalchemy import select

from ...domain.time import BEIJING, as_utc
from ...domain.wealth import business_day
from ...models.wealth import (WealthOrderORM, WealthLotORM, WealthSettlementORM,
                             WealthProductVersionORM, WealthNavORM)
from ...schemas.wealth_returns import ReturnsQuery, WealthReturns
from .wealth_asset_summary import begin_read, digest
from .transfer_service import BankError


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('金额或份额记录缺失')
    return value


def moment(value):
    if not isinstance(value, str):
        raise ValueError('缺少实际业务时点')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('实际业务时点缺少时区')
    return result.astimezone(BEIJING)


class Replay:
    def __init__(self, orders, lots, settlements, versions, navs, now):
        from .wealth_service import stable_id, scaled, nav_available_at
        self.now = now.astimezone(BEIJING)
        self.versions = {v.version_id: v for v in versions}
        self.names = {v.product_id: v.name for v in sorted(versions, key=lambda v:(v.product_id,v.version))}
        self.events = []
        self.errors = {}
        self.current_lots = [(l.lot_id, l.units_scaled, l.cost_fen, l.version_id, l.account_id) for l in lots]
        self.navs = {}
        for nav in navs:
            self.navs.setdefault(nav.version_id, []).append((nav.nav_date, as_utc(nav_available_at(nav)), nav.price))
        for rows in self.navs.values():
            rows.sort(reverse=True)
        grouped = {}
        for event in settlements:
            grouped.setdefault(event.operation_id, []).append(event)
        for order in orders:
            version = self.versions.get(order.version_id)
            if version is None:
                # Cannot assign this order to any known product safely.
                self.errors.setdefault('__unknown__', []).append('订单产品版本缺失')
                continue
            pid = version.product_id
            evidence = grouped.get(order.operation_id, [])
            # Pending/cancelled unpriced applications have no economic movement.
            if not evidence and order.status not in {'SETTLED', 'PENDING_CASH'}:
                continue
            self.errors.setdefault(pid, [])
            try:
                kinds = [e.kind for e in evidence]
                if len(kinds) != len(set(kinds)) or any(k not in {'pricing', 'cash', 'quick_cash'} for k in kinds):
                    raise ValueError('结算事件重复或类型尚未支持')
                if order.direction == 'purchase':
                    if kinds != ['pricing']:
                        raise ValueError('申购缺少唯一成交记录')
                elif not (set(kinds) in ({'pricing'}, {'pricing', 'cash'}, {'quick_cash'})):
                    raise ValueError('赎回缺少定价或到账记录')
                if order.direction == 'redeem' and order.status == 'SETTLED' and not {'cash', 'quick_cash'}.intersection(kinds):
                    raise ValueError('已结算赎回缺少到账记录')
                for event in evidence:
                    data = event.data
                    at = moment(order.details.get('business_at') if event.kind == 'quick_cash' else data.get('business_at'))
                    if at > self.now:
                        raise ValueError('结算业务时点晚于当前时点')
                    action = {'at': at, 'id': event.event_id, 'op': order.operation_id, 'pid': pid,
                              'version': order.version_id, 'account': order.account_id, 'kind': event.kind, 'direction': order.direction,
                              'fee': 0, 'input': 0, 'output': 0, 'add': None, 'remove': []}
                    if event.kind == 'cash':
                        action['output'] = integer(data['cash_fen'])
                    elif order.direction == 'purchase':
                        units = scaled(data['units'])
                        if units <= 0:
                            raise ValueError('成交份额无效')
                        paid = integer(data['gross_fen']) - integer(data['refund_fen'])
                        if paid < 0 or integer(data['net_fen']) + integer(data['fee_fen']) != data['gross_fen']:
                            raise ValueError('申购净额、费用或退款不一致')
                        action.update(input=paid, fee=integer(data['fee_fen']),
                                      add=(stable_id('LOT', order.operation_id), units, paid))
                    else:
                        action['cash'] = integer(data['cash_fen'])
                        action['fee'] = integer(data['fee_fen'])
                        action['realized'] = data['realized_pnl_fen']
                        if type(action['realized']) is not int:
                            raise ValueError('赎回收益记录缺失')
                        if not data['lots']:
                            raise ValueError('赎回成本批次缺失')
                        for item in data['lots']:
                            units = integer(item['units_scaled']) if 'units_scaled' in item else scaled(item['units'])
                            if units <= 0:
                                raise ValueError('赎回份额无效')
                            action['remove'].append((item['lot_id'], units, integer(item['cost_fen'])))
                        if sum(v[2] for v in action['remove']) != integer(data['cost_fen']):
                            raise ValueError('赎回成本与批次不一致')
                        if action['cash'] - data['cost_fen'] != action['realized']:
                            raise ValueError('赎回收益与实际现金和成本不一致')
                        if event.kind == 'quick_cash':
                            action['output'] = action['cash']
                    self.events.append(action)
            except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                self.errors[pid].append(f'{order.operation_id}：{error}')
        self.events.sort(key=lambda e: (e['at'], 0 if e['add'] else 2 if e['kind'] == 'cash' else 1, e['id']))
        # Reconcile replayed current quantities and remaining costs, including closed lots.
        for lot in lots:
            version = self.versions.get(lot.version_id)
            pid = version.product_id if version else '__unknown__'
            self.errors.setdefault(pid, [])
        for pid in list(self.errors):
            try:
                state = self.state(pid, self.now, inclusive=True)
                expected = {l.lot_id: (l.units_scaled, l.cost_fen, l.version_id, l.account_id) for l in lots
                            if self.versions.get(l.version_id) and self.versions[l.version_id].product_id == pid}
                actual = {key: tuple(value) for key, value in state['lots'].items()}
                if actual != expected:
                    raise ValueError('历史成交重建与当前批次份额或成本不一致')
            except (ValueError, KeyError) as error:
                self.errors[pid].append(str(error))

    def state(self, pid, cutoff, inclusive=False):
        lots, receivables = {}, {}
        paid = withdrawn = realized = fees = 0
        for event in self.events:
            if event['pid'] != pid or event['at'] > cutoff or (event['at'] == cutoff and not inclusive):
                continue
            paid += event['input']; withdrawn += event['output']; fees += event['fee']
            if event['add']:
                key, units, cost = event['add']
                if key in lots:
                    raise ValueError('重复申购批次')
                lots[key] = [units, cost, event['version'], event['account']]
            elif event['kind'] == 'cash':
                if receivables.pop(event['op'], None) != event['output']:
                    raise ValueError('赎回到账与应收不一致')
            else:
                for key, units, cost in event['remove']:
                    if key not in lots or units > lots[key][0] or cost > lots[key][1] or lots[key][3] != event['account'] or lots[key][2] != event['version']:
                        raise ValueError('赎回批次缺失或超过持有份额/成本')
                    lots[key][0] -= units; lots[key][1] -= cost
                realized += event['realized']
                if event['kind'] != 'quick_cash':
                    receivables[event['op']] = event['cash']
        return {'lots': lots, 'receivables': receivables, 'paid': paid, 'withdrawn': withdrawn,
                'realized': realized, 'fees': fees}

    def snapshot(self, pid, day, opening=False):
        cutoff = datetime.combine(day if opening else day + timedelta(days=1), time(), BEIJING)
        inclusive = cutoff > self.now
        cutoff = min(cutoff, self.now)
        try:
            state = self.state(pid, cutoff, inclusive=inclusive)
        except (ValueError, KeyError) as error:
            return None, {'lots': {}, 'receivables': {}, 'paid': 0, 'withdrawn': 0, 'realized': 0, 'fees': 0}, str(error), None
        if self.errors.get(pid):
            return None, state, '；'.join(self.errors[pid]), None
        total = sum(state['receivables'].values())
        valuation_dates = []
        for units, cost, version_id, account_id in state['lots'].values():
            if units == 0:
                continue
            candidates = [v for v in self.navs.get(version_id, []) if v[0] < day] if opening else [
                v for v in self.navs.get(version_id, []) if v[0] <= day]
            candidates = [v for v in candidates if v[1] < cutoff or (inclusive and v[1] == cutoff)]
            version = self.versions[version_id]
            target_day = day - timedelta(days=1) if opening else day
            try:
                while not business_day(target_day, version.rules['calendar_version']):
                    target_day -= timedelta(days=1)
            except (KeyError, ValueError):
                return None, state, '产品营业日规则缺失', None
            if not candidates or candidates[0][0] < target_day:
                return None, state, f'{version_id}：{target_day}有效净值未公布或缺失', None
            try:
                with localcontext() as context:
                    context.prec = 48
                    price = Decimal(candidates[0][2])
                    if not price.is_finite() or price <= 0:
                        raise ValueError('净值无效')
                    total += int((Decimal(units) * price / 100).quantize(Decimal('1')))
            except (ValueError, ArithmeticError):
                return None, state, '历史净值记录无效', None
            valuation_dates.append(candidates[0][0])
        return total, state, None, min(valuation_dates) if valuation_dates else None

    def profit(self, pid, day):
        end, state, reason, valuation = self.snapshot(pid, day)
        start, before, opening_reason, _ = self.snapshot(pid, day, opening=True)
        if end is None or start is None:
            return None, reason or opening_reason
        return end - start + state['withdrawn'] - before['withdrawn'] - state['paid'] + before['paid'], None


def build(replay, query, asset_scope_id):
    today = replay.now.date()
    earliest = min((e['at'].date() for e in replay.events), default=today)
    ids = sorted(replay.errors)
    if query.scope == 'product':
        if query.product_id not in ids:
            raise BankError('当前授权范围没有此具体份额的历史记录', 'NOT_FOUND', 404)
        ids = [query.product_id]
        earliest = min((e['at'].date() for e in replay.events if e['pid'] in ids), default=today)
    anchor = query.anchor_date or today
    if query.period == 'cumulative':
        start, end = earliest, today
    elif query.period == 'month':
        start, end = anchor.replace(day=1), anchor.replace(day=monthrange(anchor.year, anchor.month)[1])
    elif query.period == 'year':
        start, end = date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    else:
        start = end = anchor
    # Work only inside verified coverage. Dates outside it remain explicit unknown.
    daily = {}
    missing = set()
    for offset in range((end-start).days+1):
        day = start + timedelta(days=offset)
        if day < earliest or day > today:
            daily[day] = (None, '可核实覆盖范围以外')
            continue
        amounts = [replay.profit(pid, day) for pid in ids]
        reasons = [reason for value, reason in amounts if value is None]
        daily[day] = (None, '；'.join(reasons)) if reasons else (sum(v for v, _ in amounts), None)
        missing.update(reason for reason in reasons if reason)
    effective_end = min(end, today)
    products = []; realized = []; unrealized = []; fees = []
    period_values = []
    for pid in ids:
        values = [replay.profit(pid, day)[0] for day in daily if earliest <= day <= today]
        value = sum(values) if values and all(v is not None for v in values) else None
        if start < earliest or end > today:
            # Display only covered values, but never claim the whole requested period is complete.
            missing.add('所选期间仅覆盖部分日期')
        ending, state, reason, _ = replay.snapshot(pid, effective_end)
        cum = None if ending is None or effective_end < earliest else ending + state['withdrawn'] - state['paid']
        if reason:
            missing.add(reason)
        realized.append(None if replay.errors.get(pid) or effective_end < earliest else state['realized'])
        unrealized.append(None if ending is None or effective_end < earliest else ending - sum(state['receivables'].values()) - sum(l[1] for l in state['lots'].values()))
        try:
            before = replay.state(pid, datetime.combine(start, time(), BEIJING))
        except (ValueError, KeyError):
            before = {'fees': 0}
        fees.append(None if replay.errors.get(pid) else state['fees']-before['fees'] if start <= today else 0)
        period_values.append(value)
        products.append(dict(product_id=pid, product_name=replay.names.get(pid, '版本缺失'), period_pnl_fen=value,
                             cumulative_pnl_fen=cum, status='partial' if reason or start<earliest or end>today or value is None else 'complete', reason=reason))
    def total(values):
        return None if any(v is None for v in values) else sum(values)
    points = []
    groups = {}
    for day, value in daily.items():
        key = day.replace(day=1) if query.period == 'year' else date(day.year, 1, 1) if query.period == 'cumulative' else day
        groups.setdefault(key, []).append((day, value))
    for rows in groups.values():
        known = [v for _, (v, _) in rows if v is not None]
        reasons = sorted(set(reason for _, (_, reason) in rows if reason))
        if query.period != 'cumulative' and any(d == today for d, _ in rows):
            reasons.append('当前期间尚未结束；截至查询业务时点')
        points.append(dict(period_start=rows[0][0], period_end=rows[-1][0], pnl_fen=sum(known) if known else None,
                           status='partial' if reasons and known else 'unavailable' if reasons else 'complete', reason='；'.join(reasons) or None))
    partial = bool(missing) or start<earliest or end>today or any(v is None for v in period_values)
    if query.period != 'cumulative' and end >= today and start <= today:
        partial = True
        missing.add('当前期间尚未结束；收益截至查询业务时点')
    if start<earliest or end>today:
        missing.add('所选期间包含覆盖外日期；只汇总可核实部分')
    valuation_dates = [replay.snapshot(pid, effective_end)[3] for pid in ids]
    return WealthReturns(asset_scope_id=asset_scope_id,
        snapshot_id=digest('WRETURN-', {'scope':asset_scope_id, 'now':replay.now.isoformat(), 'events':[
            {**e,'at':e['at'].isoformat()} for e in replay.events], 'navs': {k:[(d.isoformat(), t.isoformat(), p) for d,t,p in v] for k,v in replay.navs.items()},'errors':replay.errors,'lots':replay.current_lots}),
        scope=query.scope, product_id=query.product_id, period=query.period, start_date=start, end_date=end,
        as_of=replay.now, coverage_start=None if any(replay.errors.get(pid) for pid in ids) else earliest,
        status='partial' if partial else 'complete', missing_fields=sorted(missing),
        summary=dict(period_pnl_fen=total(period_values) if ids else (0 if start<=today<=end else None),
                     cumulative_pnl_fen=None if effective_end < earliest else total([p['cumulative_pnl_fen'] for p in products]),
                     realized_pnl_fen=None if effective_end < earliest else total(realized),
                     unrealized_pnl_fen=None if effective_end < earliest else total(unrealized),
                     fees_fen=None if effective_end < earliest else total(fees)),
        points=points, products=products, data_source='simulated_bank_actual_settlements',
        valuation_as_of=min((v for v in valuation_dates if v), default=None), formula_version='actual-settlement-replay-v1').model_dump(mode='json')


async def read(db, user_id, query):
    from .wealth_service import assets
    await begin_read(db)
    facts = await assets(db, user_id)
    scope = facts['asset_summary']
    accounts = scope['account_ids']
    orders = list(await db.scalars(select(WealthOrderORM).where(WealthOrderORM.user_id == user_id, WealthOrderORM.account_id.in_(accounts))))
    lots = list(await db.scalars(select(WealthLotORM).where(WealthLotORM.user_id == user_id, WealthLotORM.account_id.in_(accounts))))
    settlements = list(await db.scalars(select(WealthSettlementORM).where(WealthSettlementORM.operation_id.in_([o.operation_id for o in orders]))))
    version_ids = {o.version_id for o in orders} | {l.version_id for l in lots}
    versions = list(await db.scalars(select(WealthProductVersionORM).where(WealthProductVersionORM.version_id.in_(version_ids))))
    navs = list(await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id.in_(version_ids))))
    return build(Replay(orders, lots, settlements, versions, navs, moment(facts['business_at'])), query, scope['asset_scope_id'])
