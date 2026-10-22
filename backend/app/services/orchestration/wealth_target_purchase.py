"""Group explicit purchase amounts using existing atomic bank execution/MFA."""
from ..audit.spans import audited
import hashlib
import json
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select
from ...integrations.wealth_authorization import lock_profile, WealthPolicyError
from ...integrations.bank_api import BankApiError
from ...models.wealth import WealthPlanORM, WealthProfileORM, WealthGoalORM
from ...schemas.wealth import InvestmentCommand
from . import wealth_plan


@audited("wealth")
async def prepare(db, user_id, bank, request):
    plan_id = 'WP' + uuid5(NAMESPACE_URL, user_id + ':' + request.request_id).hex
    payload = request.model_dump(mode='json')
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    await lock_profile(db, user_id)
    row = await db.get(WealthPlanORM, plan_id, populate_existing=True)
    if row and (row.user_id != user_id or row.data.get('target_request_hash') != fingerprint):
        raise WealthPolicyError('同一请求号不能改变申购输入', 'IDEMPOTENCY_CONFLICT', 409)
    if row:
        result = wealth_plan.plan_view(row)
        await db.rollback()
    else:
        await db.rollback()
        contexts = await bank.call('POST', '/contexts', {'subject_user_id':user_id})
        context = next((c for c in contexts['items'] if c['context_id'] == request.context_id), None)
        facts = await bank.assets(user_id)
        if not context or contexts['selected'] != request.context_id or facts.get('context_id') != request.context_id:
            raise WealthPolicyError('理财环境已变化，请刷新已选产品', 'STALE_CONTEXT', 409)
        account = next((a for a in facts['cash'] if a['account_id'] == context['account_id']
                        and a['account_type'] in {'savings','checking'}), None)
        if not account:
            raise WealthPolicyError('当前理财绑定的借记账户不可用', 'CASH_ACCOUNT_REQUIRED', 409)
        total = sum(item.amount_fen for item in request.items)
        if total > account['available_fen']:
            available = account['available_fen']
            raise WealthPolicyError(f'申购合计{total / 100:.2f}元，借记账户可用{available / 100:.2f}元，'
                f'还差{(total - available) / 100:.2f}元。请减少申购金额或向绑定借记账户补足资金后重试。',
                'INSUFFICIENT_AVAILABLE_FUNDS', 409)
        commands = []
        for index, item in enumerate(request.items):
            cmd = InvestmentCommand(**item.model_dump(), account_id=account['account_id'], direction='purchase',
                operation_id='WO'+uuid5(NAMESPACE_URL, plan_id+':'+str(index)).hex,
                plan_id=plan_id, plan_version=1)
            # Context and quote bindings are established by the bank, not by
            # assigning an unverified v2 command in the browser.
            checked = await bank.precheck(user_id, cmd)
            commands.append({'command':checked['command'], 'reason':'本人逐产品指定申购金额',
                             'status':'AWAITING_CONFIRMATION', 'depends_on':[]})
        await lock_profile(db, user_id)
        row = await db.get(WealthPlanORM, plan_id, populate_existing=True)
        if row:
            if row.data.get('target_request_hash') != fingerprint:
                raise WealthPolicyError('同一请求号不能改变申购输入', 'IDEMPOTENCY_CONFLICT', 409)
        else:
            profile = await db.get(WealthProfileORM, user_id)
            goals = list(await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id)))
            row = WealthPlanORM(plan_id=plan_id, user_id=user_id, version=1, status='AWAITING_CONFIRMATION', data={
                'manual_purchase':True, 'target_request_hash':fingerprint, 'context_id':request.context_id,
                'stage':'BUY', 'items':commands, 'history':[],
                'request':{**payload, 'kind':'target_purchase', 'account_id':account['account_id']},
                'configuration':{'investment_fen':total, 'retained_cash_fen':account['available_fen']-total,
                                 'unallocated_fen':0},
                'profile_version':profile.version, 'goal_versions':{g.goal_id:g.version for g in goals},
                'notice':'仅汇总本人指定的申购金额，一次确认与统一验证，不调用资产配置或计算推荐比例'})
            db.add(row)
            await db.flush()
            from .wealth_task import register_plan
            await register_plan(db, user_id, plan_id, request.context_id, wealth_plan.plan_view(row), commit=False)
        await db.commit()
        result = wealth_plan.plan_view(row)
    preview = None
    if result['status'] == 'AWAITING_CONFIRMATION' and all(i['status'] == 'AWAITING_CONFIRMATION' for i in result['items']):
        try:
            preview = await wealth_plan.preview_all(db, user_id, plan_id, result['version'], bank)
        except (BankApiError, WealthPolicyError) as error:
            if error.code not in {'QUOTE_CHANGED', 'AUTHORIZATION_EXPIRED', 'PREVIEW_REQUIRED'}:
                raise
            await db.rollback()
            # One bounded automatic renewal. Old confirmations are invalidated;
            # the response still requires a new explicit confirmation and MFA.
            return await wealth_plan.refresh_purchase_review(db, user_id, plan_id, result['version'], bank)
        result = wealth_plan.plan_view(await wealth_plan.owned_plan(db, user_id, plan_id))
    return {'plan':result, 'preview':preview}
