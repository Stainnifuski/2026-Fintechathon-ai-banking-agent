"""Resolve only the original sell task's reconciled proceeds; never infer from cash delta."""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
from . import wealth_task
from ...integrations.wealth_authorization import WealthPolicyError


async def resolve(db, user_id, bank, task_id, state, facts, account):
    item=state.get('fields',{}).get('investment_budget',{})
    value=item.get('value',{})
    if item.get('state')!='base_pending' or value.get('base')!='settled_proceeds':
        return state
    if not task_id or not account:
        raise WealthPolicyError('实际到账比例必须绑定原赎回任务','TASK_DEPENDENCY_PENDING',409)
    task=await wealth_task.owned(db,user_id,task_id)
    view=await wealth_task.view(db,task)
    if task.account_id!=account['account_id'] or task.data.get('goal')!='sell_then_buy':
        raise WealthPolicyError('实际到账比例不能借用其他任务或账户','TASK_DEPENDENCY_PENDING',409)
    settlements=[n for n in view['nodes'] if n['kind']=='SETTLEMENT']
    if (task.data.get('stop_new_submissions') or not settlements or
            any(n['status']!='COMPLETED' for n in settlements) or
            not any(n['kind']=='REREAD_CASH' and n['status']=='COMPLETED' for n in view['nodes'])):
        raise WealthPolicyError('须先核对原赎回实际到账并重读现金','TASK_DEPENDENCY_PENDING',409)
    operations=list(dict.fromkeys(n['output']['operation_id'] for n in settlements))
    total=0
    for operation in operations:
        order=await bank.query(user_id,operation)
        cash=order.get('details',{}).get('actual',{}).get('cash_fen')
        if (order.get('status')!='SETTLED' or order.get('direction')!='redeem' or
                order.get('account_id')!=account['account_id'] or type(cash) is not int or cash<0):
            raise WealthPolicyError('原号到账证据尚未完整','RECONCILIATION_REQUIRED',409)
        total+=cash
    result=deepcopy(state)
    budget=result['fields']['investment_budget']
    budget.update(state='explicit',base_fen=total,
        resolved_fen=int((Decimal(total)*Decimal(value['ratio'])).to_integral_value(rounding=ROUND_DOWN)),
        bank_resolution={'task_id':task_id,'operation_ids':operations,'as_of':facts['business_at']})
    result['version']+=1
    result['changed_fields']=list(dict.fromkeys([*result.get('changed_fields',[]),'investment_budget']))
    return result
