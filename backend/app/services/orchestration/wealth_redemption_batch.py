"""Explicit holding redemptions grouped under existing confirmation/MFA; no allocation."""
from ..audit.spans import audited
import hashlib
import json
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select
from ...integrations.wealth_authorization import lock_profile, WealthPolicyError
from ...models.wealth import WealthPlanORM, WealthProfileORM, WealthGoalORM, WealthConversationORM
from ...schemas.wealth import InvestmentCommand
from . import wealth_plan


@audited("wealth")
async def prepare(db, user_id, bank, request):
    plan_id = 'WR' + uuid5(NAMESPACE_URL, user_id + ':' + request.request_id).hex
    payload = request.model_dump(mode='json')
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    await lock_profile(db, user_id)
    if request.session_id:
        session = await db.get(WealthConversationORM, request.session_id, populate_existing=True)
        if not session or session.user_id != user_id or session.data.get('closed'):
            raise WealthPolicyError('会话已关闭或不可用', 'SESSION_CLOSED', 409)
        if session.data.get('context_id', 'legacy-v1') != request.items[0].context_id:
            raise WealthPolicyError('会话资金范围已变化', 'STALE_CONTEXT', 409)
    row = await db.get(WealthPlanORM, plan_id, populate_existing=True)
    if row and (row.user_id != user_id or row.data.get('redemption_request_hash') != fingerprint):
        raise WealthPolicyError('同一请求号不能改变赎回输入', 'IDEMPOTENCY_CONFLICT', 409)
    if row:
        result = wealth_plan.plan_view(row)
        await db.rollback()
    else:
        await db.rollback()
        commands = []
        for index, item in enumerate(request.items):
            converted = await bank.call('POST', '/redemptions/preview', {
                **item.model_dump(mode='json'), 'subject_user_id': user_id,
                'operation_id': 'WO' + uuid5(NAMESPACE_URL, plan_id + ':' + str(index)).hex})
            command = {**converted['preview']['command'], 'plan_id': plan_id, 'plan_version': 1}
            commands.append({'command': command, 'reason': converted['notice'],
                             'status': 'AWAITING_CONFIRMATION', 'depends_on': []})
        await lock_profile(db, user_id)
        row = await db.get(WealthPlanORM, plan_id, populate_existing=True)
        if row:
            if row.data.get('redemption_request_hash') != fingerprint:
                raise WealthPolicyError('同一请求号不能改变赎回输入', 'IDEMPOTENCY_CONFLICT', 409)
        else:
            profile = await db.get(WealthProfileORM, user_id)
            goals = list(await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id)))
            row = WealthPlanORM(plan_id=plan_id, user_id=user_id, version=1, status='AWAITING_CONFIRMATION', data={
                'manual_redemption': True, 'redemption_request_hash': fingerprint,
                'context_id': request.items[0].context_id, 'stage': 'SELL', 'items': commands,
                'configuration': {}, 'history': [], 'request': payload,
                'profile_version': profile.version, 'goal_versions': {g.goal_id:g.version for g in goals},
                'notice': '按本人逐项输入赎回；一次确认和统一验证，不计算资产配置或后续买入'})
            db.add(row)
            await db.flush()
            # Preserve the same per-operation pause/handoff/dependency checks as
            # the existing single-holding entry, while authorizing the set once.
            from .wealth_task import create_command
            for child in commands:
                await create_command(db, user_id, request.session_id or plan_id, request.items[0].context_id,
                    InvestmentCommand.model_validate(child['command']), holding_redemption=True, commit=False)
        await db.commit()
        result = wealth_plan.plan_view(row)
    preview = None
    if result['status'] == 'AWAITING_CONFIRMATION' and all(i['status'] == 'AWAITING_CONFIRMATION' for i in result['items']):
        preview = await wealth_plan.preview_all(db, user_id, plan_id, result['version'], bank)
        result = wealth_plan.plan_view(await wealth_plan.owned_plan(db, user_id, plan_id))
    return {'plan': result, 'preview': preview}
