"""A second, independently authorized BUY plan after proven instant redemptions."""
from collections import defaultdict
from decimal import Decimal
from copy import deepcopy
from ...schemas.wealth_selection import PortfolioPrepare
from . import wealth_plan,wealth_task,wealth_selection


def assert_redemption_effects(original,current,items):
    expected_units=defaultdict(Decimal);actual_units=defaultdict(Decimal)
    balances={a['account_id']:a['balance_fen'] for a in original['cash'] if a['account_type']!='credit'}
    for lot in original['lots']:expected_units[(lot['account_id'],lot['product_id'],lot['product_version'])]+=Decimal(lot['units'])
    for item in items:
        command=item['command'];result=item['bank_result']
        if command['direction']!='redeem' or result.get('status')!='SETTLED' or result.get('operation_id')!=command['operation_id']:
            wealth_selection.reject('赎回尚未全部核实到账，不能进入申购','PLANNING_REDEMPTION_NOT_SETTLED',409)
        expected_units[(command['account_id'],command['product_id'],command['product_version'])]-=Decimal(command['units'])
        cash=(result.get('details',{}).get('actual') or {}).get('cash_fen')
        if type(cash) is not int:wealth_selection.reject('无法核实赎回实际到账金额','PLANNING_REDEMPTION_NOT_SETTLED',409)
        balances[command['account_id']]+=cash
    for lot in current['lots']:actual_units[(lot['account_id'],lot['product_id'],lot['product_version'])]+=Decimal(lot['units'])
    if ({k:v for k,v in expected_units.items() if v}!={k:v for k,v in actual_units.items() if v}
        or balances!={a['account_id']:a['balance_fen'] for a in current['cash'] if a['account_type']!='credit'}
        or original['context_id']!=current['context_id']):
        wealth_selection.reject('赎回之外的资金或持仓也发生了变化，请重新生成规划；已到账现金保留。','PLANNING_EXTRA_ASSET_CHANGE',409)


async def continue_purchase(db,user_id,execution_id,version,bank,source):
    async with source.continuation_lock:
        row=await wealth_plan.owned_plan(db,user_id,execution_id)
        if row.version!=version:wealth_selection.reject('赎回执行版本已变化','STALE_PLAN',409)
        if row.data.get('stage')!='SELL' or not row.data.get('portfolio_plan',{}).get('planning'):
            wealth_selection.reject('此交易不是可接续的增量规划赎回阶段','PLANNING_CONTINUATION_INVALID',409)
        cached=row.data.get('incremental_continuation')
        if cached:return deepcopy(cached)
        task=await wealth_task.require_plan_edit(db,user_id,execution_id)
        if task and (task.status=='PAUSED' or task.data.get('stop_new_submissions') or task.data.get('session_closed')):
            wealth_selection.reject('任务已暂停或结束，请重新规划；已到账现金保留。','TASK_WRITE_BLOCKED',409)
        await db.rollback()
        progress=await wealth_plan.progress(db,user_id,execution_id,bank)
        if not progress['items'] or any(i['status']!='SETTLED' for i in progress['items']):
            wealth_selection.reject('请先核实全部赎回已到账，未知或部分完成的结果不能发起申购。','PLANNING_REDEMPTION_NOT_SETTLED',409)
        row=await wealth_plan.owned_plan(db,user_id,execution_id)
        previous=deepcopy(row.data)
        current=await bank.assets(user_id)
        assert_redemption_effects(previous['snapshot'],current,previous['items'])
        # No original consent, verification, or authorization is copied.
        plan=await source.resume_planning(db,user_id,previous['portfolio_plan'],bank)
        if any(plan['planning']['redemption_totals_fen']):
            wealth_selection.reject('到账费用或净值变化后仍需减持，请重新规划并确认；不会自动追加赎回。','PLANNING_RECALCULATION_REQUIRED',409)
        prepared=await wealth_selection.prepare(db,user_id,plan['portfolio_id'],PortfolioPrepare(
            expected_version=plan['version'],snapshot_id=plan['snapshot_id'],payment_account_id=previous.get('payment_account_id')),bank,source)
        response={'plan':plan,'prepared':prepared}
        row=await wealth_plan.owned_plan(db,user_id,execution_id)
        row.data={**row.data,'incremental_continuation':response}
        row.status='COMPLETED'
        task=await wealth_task.linked_plan_task(db,user_id,execution_id)
        if task:
            # All original bank operations were just proven settled. Close the
            # old waiting BUY gate; the new BUY graph requires its own MFA.
            from sqlalchemy import select
            from ...models.wealth import WealthTaskNodeORM
            for node in await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task.task_id)):
                if node.kind in {'SETTLEMENT','REREAD_CASH','BUY_PLAN','RESULT'}:node.status='COMPLETED'
            task.status='COMPLETED';task.waiting_reason=None
        await db.commit()
        return response
