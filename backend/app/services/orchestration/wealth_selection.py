"""Translate A's validated total-asset targets to the existing execution store.

No recommendation, risk scoring, target normalization or bank ORM access.
Current positions are valued by the trusted snapshot, not by the browser.
"""
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from copy import deepcopy
from uuid import uuid4
from sqlalchemy import select
from ...domain.wealth_limits import MAX_EXECUTION_ITEMS
from ...domain.wealth import units
from ...integrations.wealth_authorization import WealthPolicyError, lock_profile
from ...models.wealth import WealthPlanORM, WealthConversationORM, WealthAuthorizationORM, WealthProfileORM, WealthTaskORM, WealthGoalORM
from ...schemas.wealth import InvestmentCommand
from . import wealth_plan, wealth_task


def reject(message, code='INVALID_PORTFOLIO', status=422):
    raise WealthPolicyError(message, code, status)


def ratio(value):
    try:
        result = Decimal(value)
        if not isinstance(value, str) or not result.is_finite() or not 0 <= result <= 1:
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        reject('整体比例须为0至1的十进制字符串')


def execution_targets(plan,snapshot,payment_account_id=None):
    """Adapt canonical A references to B execution; never select another payer.

    A's selected_positions contain references/ratios, while advised_positions
    contain authoritative integer targets. A unique authorized debit account
    is unambiguous; multiple accounts require an explicit payment choice.
    Existing multi-account holdings are preserved for increases/maintenance.
    A reduction across multiple accounts requires an explicit distribution.
    """
    if plan['selected_positions'] and all('account_id' in p and 'target_fen' in p for p in plan['selected_positions']):
        return plan,None # compatible already-bound legacy provider
    chosen={(p['product_id'],p['product_version']) for p in plan['selected_positions']}
    missing=[p for p in plan.get('advised_positions',[]) if
        (p['product_id'],p['product_version']) not in chosen and p['target_fen']!=p.get('current_fen',0)]
    if missing:
        reject('方案中还有未纳入已校验选择的建议产品。请先在推荐结果中勾选需要的产品，点击“校验所选产品与比例”，再准备差额；尚未下单或占款。',
               'PORTFOLIO_SELECTION_REQUIRED',409)
    accounts={a['account_id']:a for a in snapshot['accounts'] if a['account_type']!='credit'}
    if payment_account_id is not None and payment_account_id not in accounts:
        reject('付款账户不属于当前授权范围','ACCOUNT_CONTEXT_MISMATCH',409)
    payer=payment_account_id or (next(iter(accounts)) if len(accounts)==1 else None)
    advice={(p['product_id'],p['product_version']):p for p in plan.get('advised_positions',[])}
    if len(advice)!=len(plan.get('advised_positions',[])):reject('目标资料重复','INVALID_PORTFOLIO',409)
    references={(p['product_id'],p['product_version']):p for p in plan['selected_positions']}
    if len(references)!=len(plan['selected_positions']):reject('重复具体产品','INVALID_PORTFOLIO',409)
    # Existing holding targets are part of the whole-asset proposal, not new
    # purchase intent. They remain subject to ordinary exit/lot permissions.
    for key,p in advice.items():
        if p.get('account_ids') and key not in references:
            references[key]={'product_id':key[0],'product_version':key[1],'share_class':p['share_class'],
                'target_ratio_total':format(Decimal(p['target_fen'])/Decimal(snapshot['basis_fen']),'f')}
    rows=[]
    for key,ref in references.items():
        detail=advice.get(key)
        if not detail or type(detail.get('target_fen')) is not int or type(detail.get('disclosure_version')) is not int:
            reject('缺少A返回的具体目标或披露版本','PORTFOLIO_TARGET_DETAIL_MISSING',409)
        target=detail['target_fen']
        if abs(Decimal(snapshot['basis_fen'])*ratio(ref['target_ratio_total'])-target)>1:
            reject('A目标金额与引用比例不一致','INVALID_PORTFOLIO',409)
        held=[p for p in snapshot['positions'] if (p['product_id'],p['product_version'])==key]
        if any(p['market_value_fen'] is None for p in held):reject('持仓估值缺失','VALUATION_INCOMPLETE',409)
        total=sum(p['market_value_fen'] for p in held)
        allocations={p['account_id']:p['market_value_fen'] for p in held}
        if target>=total:
            if target>total:
                if payer is None:reject('请选择本次付款账户；不会跨账户自动付款','PAYMENT_ACCOUNT_REQUIRED',409)
                allocations[payer]=allocations.get(payer,0)+target-total
            elif not allocations:
                # Retain a zero target so its actual risk is still checked.
                if payer is None:reject('请选择本次付款账户','PAYMENT_ACCOUNT_REQUIRED',409)
                allocations[payer]=0
        elif len(held)==1:allocations={held[0]['account_id']:target}
        else:reject('同产品跨账户减持需要明确账户分配，不能自动卖出','ACCOUNT_ALLOCATION_REQUIRED',409)
        for account_id,value in allocations.items():
            rows.append({**ref,'account_id':account_id,'target_fen':value,'target_ratio_total':format(Decimal(value)/Decimal(snapshot['basis_fen']),'f'),
                'family_id':detail['family_id'],'bucket':detail['bucket'],'name':detail['name'],'disclosure_version':detail['disclosure_version']})
    mapped=deepcopy(plan);mapped['selected_positions']=rows
    return mapped,payer


def difference_actions(plan, snapshot):
    """Deterministic execution arithmetic only; A decides the target weights.

Unmentioned existing positions are retained, never silently sold. Every planned
position binds a payment account; reading a scope grants no transfer authority.
"""
    basis = snapshot.get('basis_fen')
    if not snapshot.get('valuation_complete') or type(basis) is not int or basis <= 0:
        reject('资产估值不完整，不能准备整体交易', 'VALUATION_INCOMPLETE', 409)
    if plan.get('basis_fen') != basis or plan.get('missing_fields'):
        reject('整体方案的资产基准或数据已失效', 'STALE_SNAPSHOT', 409)
    accounts = {a['account_id']: a for a in snapshot['accounts'] if a['account_type'] != 'credit'}
    positions = {(p['account_id'], p['product_id'], p['product_version']): p for p in snapshot['positions']}
    if len(positions) != len(snapshot['positions']):
        reject('快照必须按账户和产品版本聚合持仓')
    targets, seen_families, actions = {}, {}, []
    for item in plan['selected_positions']:
        key = (item['account_id'], item['product_id'], item['product_version'])
        if key in targets or item['family_id'] in seen_families and seen_families[item['family_id']]!=item['product_id']:
            reject('同一家族只能选择一种具体份额')
        if item['account_id'] not in accounts:
            reject('付款账户不属于当前授权资产范围', 'ACCOUNT_CONTEXT_MISMATCH', 409)
        target = item['target_fen']
        if type(target) is not int or abs(Decimal(basis)*ratio(item['target_ratio_total'])-target)>1:
            reject('目标金额与整体比例不一致')
        targets[key] = item
        seen_families[item['family_id']]=item['product_id']
        current = positions.get(key)
        current_fen = current['market_value_fen'] if current else 0
        if current_fen is None:
            reject('持仓缺少可信估值', 'VALUATION_INCOMPLETE', 409)
        delta = target - current_fen
        if delta == 0:
            continue
        action = {**item, 'current_fen': current_fen, 'difference_fen': delta,
                  'direction': 'purchase' if delta > 0 else 'redeem',
                  'amount_fen': delta if delta > 0 else 0, 'units': '0.0000', 'status': 'READY'}
        if delta < 0:
            if current is None or not current.get('nav'):
                reject('缺少可信净值，不能换算赎回份额', 'NAV_UNAVAILABLE', 409)
            count = units(current['units']) if target == 0 else (Decimal(-delta) / 100 / Decimal(current['nav'])).quantize(Decimal('0.0001'), rounding=ROUND_DOWN)
            if count <= 0 or count > units(current['redeemable_units'], positive=False):
                reject('所需减持超过可赎份额；请修订目标，不能自动缩小赎回', 'INSUFFICIENT_REDEEMABLE_UNITS', 409)
            action['units'] = format(count, '.4f')
        actions.append(action)
    retained = sum(p['market_value_fen'] for key, p in positions.items() if key not in targets)
    residual = plan['cash_residual_fen']
    if type(residual) is not int or residual < 0:
        reject('保留现金必须为非负整数分')
    if sum(i['target_fen'] for i in targets.values()) + retained + residual + snapshot['receivable_fen'] != basis:
        reject('目标、保留持仓、现金和应收须与整体资产一致；不能把旧仓再加在100%目标之外')
    buckets = plan['buckets']
    if {b['bucket'] for b in buckets} != {'cash_management', 'stable', 'growth'} or len(buckets) != 3:
        reject('整体目标必须包含三个板块')
    for bucket in buckets:
        if not ratio(bucket['min_ratio']) <= ratio(bucket['target_ratio']) <= ratio(bucket['max_ratio']):
            reject('板块比例不在A校验区间内')
        target = sum(i['target_fen'] for i in targets.values() if i['bucket'] == bucket['bucket'])
        target += sum(p['market_value_fen'] for key, p in positions.items() if key not in targets and p['bucket'] == bucket['bucket'])
        if bucket['bucket'] == 'cash_management':
            target += residual + snapshot['receivable_fen']
        if bucket['target_fen'] != target or abs(Decimal(basis) * ratio(bucket['target_ratio']) - target) > 1:
            reject('板块汇总与已校验的整体目标不一致')
    if len(actions) > MAX_EXECUTION_ITEMS:
        reject('执行集合超出当前接口容量；不会拆分本人授权')
    sells = [a for a in actions if a['direction'] == 'redeem']
    for action in actions:
        if sells and action['direction'] == 'purchase':
            action['status'] = 'WAITING_SETTLEMENT_AND_REREAD_CASH'
    if not sells:
        for account_id, account in accounts.items():
            required = sum(a['amount_fen'] for a in actions if a['account_id'] == account_id)
            if required > account['available_fen']:
                held=account.get('held_fen')
                occupancy=f"，其中已有占款 ¥{held/100:.2f}" if held is not None else ''
                reject(f"付款账户当前可用 ¥{account['available_fen']/100:.2f}{occupancy}；本次需支付 ¥{required/100:.2f}，还差 ¥{(required-account['available_fen'])/100:.2f}。请降低配置目标或等待原委托资金实际可用后重新规划；不会使用其他账户或尚未到账资金支付",
                       'INSUFFICIENT_AVAILABLE_FUNDS', 409)
    return actions


def require_no_pending_target_exposure(plan, orders):
    """Unrelated legacy orders consume available cash, not a global workspace lock.

    A pending price/receipt cannot be treated as a zero holding of the same
    product. Keep that specific target blocked until its original order resolves.
    Compare concrete code across versions; never guess its future NAV or units.
    """
    targets={p['product_id'] for p in plan['selected_positions']}
    conflicts=[]
    for order in orders:
        # PENDING_CASH has already reduced units and recorded a receivable in
        # bank.assets; it must not freeze a new target or be spent as cash.
        if order['status'] not in {'ACCEPTED','PENDING_PRICING'}:continue
        version=order.get('version_id')
        if not isinstance(version,str) or ':' not in version:
            if targets:
                reject('付款账户的旧委托资料暂不完整，请先刷新银行事实再准备预览；不会忽略已有占款',
                    'PENDING_ORDER_IDENTITY_UNAVAILABLE',409)
            continue
        code=version.rsplit(':',1)[0]
        if code in targets:conflicts.append(code)
    if conflicts:
        codes='、'.join(sorted(set(conflicts)))
        reject(f'所选产品 {codes} 仍有 {len(conflicts)} 笔旧委托待处理，暂不能按当前持仓重复计算这些产品的配置差额。请等待原委托完成后重新规划，或改选其他产品并使用当前可用现金；原委托和占款保持不变',
            'PENDING_PRODUCT_EXPOSURE',409)


def prepared_view(row):
    return {'portfolio_id': row.data['portfolio_id'], 'portfolio_version': row.data['portfolio_version'],
            'execution_plan_id': row.plan_id, 'execution_plan_version': row.version,
            'actions': row.data['portfolio_actions'], 'stage': row.data['stage'],
            'execution_plan': wealth_plan.plan_view(row)}


async def prepare(db, user_id, portfolio_id, request, bank, source):
    plan = await source.owned_plan(db, user_id, portfolio_id)
    if plan['portfolio_id'] != portfolio_id or plan['version'] != request.expected_version:
        reject('整体方案已更新，请重新核对', 'STALE_PORTFOLIO', 409)
    snapshot = await source.snapshot(db, user_id, plan['context_id'], bank)
    if (snapshot['snapshot_id'] != request.snapshot_id or plan['snapshot_id'] != request.snapshot_id
            or snapshot['asset_scope_id'] != plan['asset_scope_id'] or snapshot['context_id'] != plan['context_id']):
        reject('资产快照或范围已更新，请先重新修订整体目标', 'STALE_SNAPSHOT', 409)
    facts = await bank.assets(user_id)
    # Include zero/tiny targets and all relevant holdings. The existing loop
    # below still rejects unavailable or changed selected products explicitly.
    relevant=sorted({p['product_id'] for p in plan['selected_positions']} |
                    {p['product_id'] for p in snapshot['positions']})
    products = await bank.selected_products(user_id,relevant) if hasattr(bank,'selected_products') else await bank.products(user_id)
    real_accounts = {a['account_id']: a for a in facts['cash'] if a['account_type'] != 'credit'}
    if facts['context_id'] != plan['context_id'] or set(snapshot['account_ids']) != set(real_accounts):
        reject('整体资产范围与当前银行环境不一致', 'ACCOUNT_CONTEXT_MISMATCH', 409)
    if any(a != real_accounts.get(a['account_id']) for a in snapshot['accounts']):
        reject('现金或冻结已变化，请重新读取事实', 'STALE_SNAPSHOT', 409)
    # Reconcile trusted snapshot positions against current bank lots, including
    # their IDs, valuation and redeemable units. Snapshot is never browser input.
    grouped = {}
    for lot in facts['lots']:
        if type(lot.get('product_version')) is not int or lot['product_version']<1:
            reject('持仓缺少明确产品历史版本','PRODUCT_VERSION_UNAVAILABLE',409)
        key = (lot['account_id'], lot['product_id'], lot['product_version'])
        group = grouped.setdefault(key, {'lots': set(), 'value': 0, 'units': Decimal(0), 'redeemable': Decimal(0), 'nav': lot['nav']})
        if lot['market_value_fen'] is None:
            reject('银行持仓缺少估值', 'VALUATION_INCOMPLETE', 409)
        group['lots'].add(lot['lot_id']); group['value'] += lot['market_value_fen']
        group['units'] += units(lot['units']); group['redeemable'] += units(lot['redeemable_units'], positive=False)
    if len(grouped) != len(snapshot['positions']):
        reject('持仓范围已变化', 'STALE_SNAPSHOT', 409)
    for position in snapshot['positions']:
        group = grouped.get((position['account_id'], position['product_id'], position['product_version']))
        if not group or (set(position['lot_ids']), position['market_value_fen'], units(position['units']),
                         units(position['redeemable_units'], positive=False), position['nav']) != (
                         group['lots'], group['value'], group['units'], group['redeemable'], group['nav']):
            reject('持仓事实已变化', 'STALE_SNAPSHOT', 409)
    basis = sum(a['balance_fen'] for a in real_accounts.values()) + sum(g['value'] for g in grouped.values()) + facts['receivable_fen']
    if snapshot['basis_fen'] != basis or snapshot['receivable_fen'] != facts['receivable_fen']:
        reject('整体资产或应收已变化', 'STALE_SNAPSHOT', 409)
    executable,payment_account_id=execution_targets(plan,snapshot,request.payment_account_id)
    require_no_pending_target_exposure(executable,facts['orders'])
    actions = difference_actions(executable, snapshot)
    catalog = {p['product_id']: p for p in products}
    # The preceding reads are detached facts. End their SQLite read snapshot
    # before acquiring the existing writer lock; another prepare/revision may
    # have committed in the meantime. The locked post-precheck reread below
    # still rejects changed plans, assets and execution mappings.
    await db.rollback()
    await lock_profile(db,user_id)
    for item in executable['selected_positions']:
        product = catalog.get(item['product_id'])
        if product is None or product['version'] != item['product_version'] or product['disclosure']['version'] != item['disclosure_version']:
            reject('所选产品或披露版本已变化', 'RULE_VERSION_CHANGED', 409)
        if item.get('share_class')!=product.get('share_class',product['rules'].get('share_class')):
            reject('所选具体份额与银行当前份额不一致','SELECTED_SHARE_IDENTITY_MISMATCH',409)
        existing=next((p for p in snapshot['positions'] if (p['account_id'],p['product_id'],p['product_version'])==(item['account_id'],item['product_id'],item['product_version'])),None)
        # A zero/tiny target is still a newly selected product. Check its actual
        # grade before difference_actions can silently omit a zero-sized order.
        # Maintaining/reducing a real existing holding remains an exit operation.
        if existing is None or item['target_fen']>existing['market_value_fen']:
            from ...integrations.wealth_authorization import require_suitability
            await require_suitability(db,user_id,product['product_risk_level'])
    await lock_profile(db, user_id)
    rows = list(await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id == user_id)))
    previous = next((row for row in rows if row.data.get('portfolio_id') == portfolio_id), None)
    if previous:
        await wealth_task.require_plan_edit(db, user_id, previous.plan_id)
        if previous.data['portfolio_version'] == plan['version'] and previous.data['portfolio_snapshot_id'] == snapshot['snapshot_id'] and previous.data.get('payment_account_id')==payment_account_id:
            return prepared_view(previous)
        if any(i['status'] != 'AWAITING_CONFIRMATION' for i in previous.data['items']):
            if previous.data['stage'] != 'SELL' or not all(i['status'] == 'SETTLED' for i in previous.data['items']):
                reject('已提交的原号须先核对，不能覆盖或再次购买', 'RECONCILIATION_REQUIRED', 409)
            task = await wealth_task.linked_plan_task(db, user_id, previous.plan_id)
            if not task:
                reject('缺少结算依赖记录', 'RECONCILIATION_REQUIRED', 409)
            from ...models.wealth import WealthTaskNodeORM
            nodes = list(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id == task.task_id)))
            if not any(n.kind == 'REREAD_CASH' and n.status == 'COMPLETED' for n in nodes):
                reject('须等待SETTLEMENT完成并重读实际现金', 'RECONCILIATION_REQUIRED', 409)
        execution_id, execution_version = previous.plan_id, previous.version + 1
        history = deepcopy(previous.data.get('history', []))
        if previous.data['stage'] == 'SELL' and all(i['status'] == 'SETTLED' for i in previous.data['items']):
            history.append({'version': previous.version, 'stage': 'SELL', 'items': deepcopy(previous.data['items'])})
        session_id = previous.data['session_id']
        previous_data, previous_status = deepcopy(previous.data), previous.status
    else:
        execution_id, execution_version, history = 'WP'+uuid4().hex, 1, []
        session = next((s for s in await db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id == user_id))
                        if s.data.get('context_id') == plan['context_id'] and not s.data.get('closed')), None)
        session_id = session.session_id if session else 'WC'+uuid4().hex
    had_previous = previous is not None
    if sum(len(h['items']) for h in history) + len(actions) > MAX_EXECUTION_ITEMS:
        reject('完整执行历史超出接口容量；不会暗拆授权')
    stage = 'SELL' if any(a['direction'] == 'redeem' for a in actions) else 'BUY'
    active = [a for a in actions if a['direction'] == ('redeem' if stage == 'SELL' else 'purchase')]
    items = []
    await db.rollback()  # Bank HTTP uses its own transaction; hold no SQLite writer lock.
    for action in active:
        command = InvestmentCommand(operation_id='WO'+uuid4().hex, direction=action['direction'],
            product_id=action['product_id'], product_version=action['product_version'], disclosure_version=action['disclosure_version'],
            account_id=action['account_id'], amount_fen=action['amount_fen'], units=action['units'], plan_id=execution_id, plan_version=execution_version)
        preview = await bank.precheck(user_id, command)
        command = InvestmentCommand.model_validate(preview['command'])
        items.append({'command': command.model_dump(), 'reason': '整体目标与当前持仓的差额', 'status': 'AWAITING_CONFIRMATION', 'depends_on': []})
    # The user lock serializes prepare calls. Re-read both source and mapping after
    # remote prechecks, so a concurrent revision cannot overwrite newer work.
    await lock_profile(db, user_id)
    current_plan = await source.owned_plan(db, user_id, portfolio_id)
    if current_plan != plan:
        reject('预检期间整体目标已修订', 'STALE_PORTFOLIO', 409)
    current_snapshot = await source.snapshot(db, user_id, plan['context_id'], bank)
    if current_snapshot['snapshot_id'] != snapshot['snapshot_id']:
        reject('预检期间银行资产已变化，请重新修订', 'STALE_SNAPSHOT', 409)
    if had_previous:
        current_row = await wealth_plan.owned_plan(db, user_id, execution_id)
        if (current_row.version != execution_version - 1 or current_row.status != previous_status
                or current_row.data != previous_data):
            reject('执行方案已更新', 'STALE_PLAN', 409)
        row = current_row
        await wealth_task.require_plan_edit(db, user_id, row.plan_id)
        for auth in await db.scalars(select(WealthAuthorizationORM).where(WealthAuthorizationORM.user_id == user_id)):
            if auth.command.get('plan_id') == row.plan_id and auth.status != 'CONSUMED':
                auth.status = 'EXPIRED'
    else:
        concurrent = next((p for p in await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id == user_id))
                           if p.data.get('portfolio_id') == portfolio_id), None)
        if concurrent:
            if concurrent.data['portfolio_version'] == plan['version']:
                return prepared_view(concurrent)
            reject('整体目标已有新执行映射', 'STALE_PLAN', 409)
        row = WealthPlanORM(plan_id=execution_id, user_id=user_id, version=1, status='DRAFT', data={})
        db.add(row)
        if await db.get(WealthConversationORM, session_id) is None:
            db.add(WealthConversationORM(session_id=session_id, user_id=user_id, version=1,
                data={'context_id': plan['context_id'], 'history': [], 'pending': {}, 'candidates': []}))
    # New operation IDs are not in the graph yet. Do not ask submission permission
    # until register_plan has bound them; still honor human/paused control now.
    affected_accounts = {i['command']['account_id'] for i in items}
    for task in await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id == user_id,
            WealthTaskORM.status.not_in(wealth_task.TERMINAL))):
        if not wealth_task.current_workspace_task(task):continue
        if task.account_id in affected_accounts and (task.control_mode != 'AGENT' or task.status == 'PAUSED' or task.data.get('stop_new_submissions')):
            reject('人工接管或暂停任务尚未交还，不能准备新交易', 'TASK_WRITE_BLOCKED', 409)
    profile = await db.get(WealthProfileORM, user_id)
    goals = list(await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id)))
    row.version, row.status = execution_version, 'AWAITING_CONFIRMATION' if items else 'COMPLETED'
    row.data = {'portfolio_id': portfolio_id, 'portfolio_version': plan['version'], 'portfolio_snapshot_id': snapshot['snapshot_id'],'context_id':plan['context_id'],
        'portfolio_actions': actions, 'portfolio_plan': plan, 'portfolio_snapshot': snapshot, 'session_id': session_id,'payment_account_id':payment_account_id,
        'request': {'kind': 'adjustment', 'account_id': active[0]['account_id'] if active else snapshot['account_ids'][0]},
        'stage': stage, 'items': items, 'history': history, 'snapshot': facts,
        'profile_version': profile.version if profile else 1,
        'goal_versions': {g.goal_id: g.version for g in goals},
        'configuration': {'investment_fen': sum(a['amount_fen'] for a in active), 'retained_cash_fen': plan['cash_residual_fen'], 'unallocated_fen': 0},
        'notice': '整体差额尚未交易；本人确认和统一验证码后才能提交。卖出后须到账、重读现金并重新确认买入。'}
    await db.flush()
    if items:
        await wealth_task.register_plan(db, user_id, session_id, plan['context_id'], wealth_plan.plan_view(row), commit=False)
    await db.commit()
    return prepared_view(row)
