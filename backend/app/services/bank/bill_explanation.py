"""Exact arithmetic attribution, with scoped transaction evidence."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from .bill_service import Tx, CONSUMPTION, LABELS, category_expr, interval, transaction_view, money


def rounded(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


async def explain_changes(db, predicates, bounds):
    groups = defaultdict(lambda: [[], []])
    for index, (start, end) in enumerate([(bounds['start'], bounds['end_exclusive']),
                                         (bounds['previous_start'], bounds['previous_end_exclusive'])]):
        rows = (await db.execute(select(Tx, category_expr()).where(
            *interval(predicates, start, end), Tx.type.in_(CONSUMPTION)).order_by(Tx.timestamp.desc(), Tx.tx_id))).all()
        for tx, cat in rows:
            groups[(cat, tx.counterparty)][index].append(tx)
    drivers = []
    for (category, merchant), (current, previous) in groups.items():
        cur, prev = sum(t.amount_fen for t in current), sum(t.amount_fen for t in previous)
        cn, pn = len(current), len(previous)
        change = cur - prev
        if not change:
            continue
        if cn and pn:
            frequency = rounded(Decimal(cn - pn) * Decimal(prev) / pn)
            unit = change - frequency  # rounding residual stays here: exact reconciliation
            entry = 0
            text = f'交易次数 {pn} → {cn}；次数变化贡献 {money(frequency)}，平均单笔金额变化贡献 {money(unit)}。'
        else:
            frequency = unit = 0
            entry = change
            text = f"{'本期出现，上期无同商户同分类记录' if cn else '上期有记录，本期未出现'}，差额 {money(entry)}。"
        large = cn == 1 and pn == 0 and category != 'subscription' and cur >= 100000
        if large:
            text += '这是本期新出现的单笔大额消费；尚不能认定长期不再发生。'
        drivers.append(dict(category=category, merchant=merchant, current_fen=cur, previous_fen=prev,
                            current_count=cn, previous_count=pn, change_fen=change,
                            frequency_effect_fen=frequency, unit_amount_effect_fen=unit, entry_exit_effect_fen=entry,
                            new_large_single=large, explanation=text,
                            current_evidence=[transaction_view(t) for t in current[:5]],
                            previous_evidence=[transaction_view(t) for t in previous[:5]]))
    drivers.sort(key=lambda d: (-abs(d['change_fen']), d['category'], d['merchant']))
    return dict(total_change_fen=sum(d['change_fen'] for d in drivers),
                frequency_effect_fen=sum(d['frequency_effect_fen'] for d in drivers),
                unit_amount_effect_fen=sum(d['unit_amount_effect_fen'] for d in drivers),
                entry_exit_effect_fen=sum(d['entry_exit_effect_fen'] for d in drivers), drivers=drivers,
                notices=['按商户原始名称及消费类别比较；商户名称不同不自动合并。',
                         '次数指交易笔数，平均单笔金额不是商品单价；分解描述统计变化，不推断购买动机。',
                         '每组展示本期及上期各最多5笔依据，组内金额和次数包含全部匹配流水。'])
