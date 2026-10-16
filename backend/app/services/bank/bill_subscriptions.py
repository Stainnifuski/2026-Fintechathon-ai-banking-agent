"""Subscription charge evidence. Links only by owned debit account and exact merchant."""
from .account_scope import isolated_account_ids

from collections import defaultdict
from sqlalchemy import select
from ...models.subscription import SubscriptionORM
from ...schemas.bills import BillQuery
from .bill_service import Tx, aware, bounds, scope, interval, transaction_view, money


async def subscription_insights(db, user_id, query: BillQuery, *, now=None):
    query = query.model_copy(update={'category': None})
    b = bounds(query, now)
    predicates = await scope(db, user_id, query)
    records = (await db.scalars(select(Tx).where(*interval(predicates, b['previous_start'], b['end_exclusive']),
                                                Tx.type == 'subscription').order_by(Tx.timestamp.desc(), Tx.tx_id))).all()
    groups = defaultdict(lambda: {'current': [], 'previous': [], 'subscriptions': []})
    for tx in records:
        group = groups[(tx.account_id, tx.counterparty)]
        when = aware(tx.timestamp)
        if b['start'] <= when < b['end_exclusive']:
            group['current'].append(tx)
        elif b['previous_start'] <= when < b['previous_end_exclusive']:
            group['previous'].append(tx)
    # Use the bank scope for account eligibility, including research-account exclusion.
    from ...models.account import AccountORM
    from ...models.wealth_research import WealthContextORM
    subs = (await db.scalars(select(SubscriptionORM).join(AccountORM, AccountORM.account_id == SubscriptionORM.debit_account_id).where(
        SubscriptionORM.user_id == user_id, AccountORM.user_id == user_id,
        AccountORM.account_id.not_in(isolated_account_ids()),
        *([AccountORM.account_id == query.account_id] if query.account_id else [])))).all()
    for sub in subs:
        groups[(sub.debit_account_id, sub.merchant_name)]['subscriptions'].append(sub)
    merchant_active = defaultdict(list)
    for sub in subs:
        if sub.status == 'ACTIVE':
            merchant_active[sub.merchant_name].append(sub)
    items = []
    for (account, merchant), group in sorted(groups.items()):
        current, previous, relations = group['current'], group['previous'], group['subscriptions']
        active = [s for s in relations if s.status == 'ACTIVE']
        flags, explanations = [], []
        if current and previous and current[0].amount_fen > previous[0].amount_fen:
            flags.append('charge_increase')
            explanations.append(f'最近单笔实际扣费由 {money(previous[0].amount_fen)} 增至 {money(current[0].amount_fen)}；不代表已确认合同涨价。')
        ordered = sorted(current, key=lambda t: (aware(t.timestamp), t.tx_id))
        recent = {}
        for tx in ordered:
            last = recent.get(tx.amount_fen)
            if last and (aware(tx.timestamp) - aware(last.timestamp)).total_seconds() <= 300:
                flags.append('possible_duplicate')
                explanations.append(f'同账户同商户两笔 {money(tx.amount_fen)} 扣费在5分钟内发生，请核对。')
                break
            recent[tx.amount_fen] = tx
        if len(merchant_active[merchant]) > 1:
            flags.append('multiple_active')
            explanations.append('同商户存在多条活跃订阅关系，可能对应不同套餐或账户；需要本人核对。')
        if not relations and current:
            flags.append('unlinked_charge')
            explanations.append('存在实际订阅扣费，但未找到同账户同商户的订阅关系，不能从此处直接取消。')
        if len(relations) > 1:
            flags.append('ambiguous_link')
            explanations.append('此组对应多条订阅关系，流水没有订阅ID，不能确定每笔属于哪条关系。')
        if len(relations) == 1 and relations[0].status == 'CANCELLED' and relations[0].cancelled_at:
            after = [t for t in current if aware(t.timestamp) > aware(relations[0].cancelled_at)]
            if after:
                flags.append('charge_after_cancellation')
                explanations.append('取消记录之后仍有已完成扣费，请核对实际流水时间和商户合同。')
        last = (current or previous)
        if len(active) == 1 and last and active[0].currency == 'CNY' and active[0].amount_fen != last[0].amount_fen:
            flags.append('plan_differs')
            explanations.append(f'当前计划金额 {money(active[0].amount_fen)} 与最近实际扣费 {money(last[0].amount_fen)} 不同；计划金额并非已扣款。')
        items.append(dict(account_id=account, merchant=merchant,
                          subscription_ids=[s.subscription_id for s in relations],
                          active_subscription_ids=[s.subscription_id for s in active],
                          configured_monthly_fen=sum(s.amount_fen for s in active if s.currency == 'CNY' and s.billing_cycle == 'MONTHLY'),
                          current_fen=sum(t.amount_fen for t in current), previous_fen=sum(t.amount_fen for t in previous),
                          current_count=len(current), previous_count=len(previous), flags=flags,
                          explanations=explanations or ['本期未触发订阅专项规则；计划与实际扣费分开展示。'],
                          current_evidence=[transaction_view(t) for t in current[:5]],
                          previous_evidence=[transaction_view(t) for t in previous[:5]]))
    items.sort(key=lambda item: (not bool(item['flags']), -item['current_fen'], item['merchant'], item['account_id']))
    return dict(as_of=b['as_of'], items=items, notices=[
        '按当前账户范围分析订阅，与消费类别筛选独立；实际扣费只计已完成流水。',
        '订阅关系按当前状态展示；历史金额按所选账期及上期统计。',
        '商户匹配依赖同账户及完全相同名称，未匹配不表示没有真实订阅。',
        '每组各展示最多5笔本期和上期流水；取消需在订阅页核对并本人确认，不退回历史扣费。'])
