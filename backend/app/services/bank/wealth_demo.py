"""本人disposable轮次管理；普通银行账户永远不能进入测试写工具。"""
import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from ...domain.money import exact_fen
from ...domain.time import utc_now, utc_iso
from ...models import AccountORM, TransactionORM, AuditLogORM
from ...models.wealth_research import WealthContextORM, WealthContextSelectionORM, WealthDemoReceiptORM, WealthChatRequestORM
from ...models.wealth import (WealthOrderORM, WealthLotORM, WealthCashHoldORM, WealthUnitHoldORM, WealthSettlementORM,
    WealthAuthorizationORM, WealthVerificationORM, WealthExecutionORM, WealthConversationORM, WealthPlanORM, WealthQuickQuotaORM, WealthLedgerReviewORM,
    WealthTaskORM, WealthTaskNodeORM, WealthTaskControlORM)
from ...integrations.wealth_authorization import lock_profile
from .transfer_service import BankError
from .available_funds import available_cash, held_cash
from .audit_service import record_bank_event


def enabled() -> bool:
    return os.getenv("WEALTH_DEMO_TOOLS_ENABLED", "false").lower() == "true"


async def require_active(db: AsyncSession, account_id: str) -> None:
    """旧worker/API记账前重验归档屏障，不伪造订单取消。"""
    context = await db.scalar(select(WealthContextORM).where(WealthContextORM.account_id == account_id))
    if context and context.data.get("run_state", "ACTIVE") != "ACTIVE":
        raise BankError("测试轮次已归档，不可继续写入", "ARCHIVED_RUN", 409)


async def context_owned(db: AsyncSession, user_id: str, run_id: str, *, archived: bool = False) -> WealthContextORM:
    if not enabled():
        raise BankError("测试工具未启用", "DEMO_TOOLS_DISABLED", 403)
    context = await db.get(WealthContextORM, run_id)
    if context is None:
        receipts = (await db.scalars(select(WealthDemoReceiptORM).where(
            WealthDemoReceiptORM.user_id == user_id, WealthDemoReceiptORM.action == "delete"))).all()
        tombstone = any(run_id in row.result.get('deleted_run_ids', []) for row in receipts)
        raise BankError("轮次已删除或不存在", "GONE" if tombstone else "NOT_FOUND", 410 if tombstone else 404)
    if context.user_id != user_id or context.data.get("disposable") is not True:
        raise BankError("不属于本人可处置测试范围", "DEMO_SCOPE_DENIED", 403)
    if (context.data.get("run_state", "ACTIVE") == "ARCHIVED") != archived:
        raise BankError("轮次状态不符合操作要求", "DEMO_SCOPE_DENIED", 409)
    return context


async def current(db: AsyncSession, user_id: str) -> dict:
    selection = await db.get(WealthContextSelectionORM, user_id)
    if not enabled() or not selection or selection.context_id == "legacy-v1":
        return {"enabled":enabled(),"writable":False}
    ctx = await context_owned(db, user_id, selection.context_id)
    account = await db.get(AccountORM, ctx.account_id)
    return {"enabled":True,"writable":True,"run_id":ctx.context_id,"epoch":ctx.version,
        "expected_version":ctx.version,"account_id":ctx.account_id,"fixture_version":ctx.data.get("fixture_version",ctx.dataset_key),
        "balance_fen":account.balance_fen,"held_fen":await held_cash(db,ctx.account_id),
        "available_fen":await available_cash(db,ctx.account_id,account.balance_fen),"business_at":ctx.data["business_at"],"scope":"disposable_simulation"}


async def receipt(db: AsyncSession, user_id: str, action: str, body: dict) -> tuple[str, str, dict | None]:
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 64:
        raise BankError("缺少合法幂等请求号", "INVALID_REQUEST", 422)
    key = user_id + ":" + request_id
    if len(key)>96:key=hashlib.sha256(key.encode()).hexdigest()
    digest = hashlib.sha256(json.dumps({"action":action, "body":body},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    row = await db.get(WealthDemoReceiptORM,key)
    if row and (row.digest != digest or row.action != action):
        raise BankError("请求号参数冲突", "IDEMPOTENCY_CONFLICT", 409)
    if row and row.result.get('gone') is True:
        raise BankError('原测试轮次已物理删除', 'GONE', 410)
    return key, digest, row.result if row else None


async def invalidate(db: AsyncSession, user_id: str, account_id: str) -> None:
    rows = (await db.scalars(select(WealthAuthorizationORM).where(WealthAuthorizationORM.user_id == user_id,
        WealthAuthorizationORM.status.in_(["AWAITING_CONFIRMATION","AWAITING_MFA","VALID"])))).all()
    for row in rows:
        if row.command.get("account_id") == account_id:
            row.status = "EXPIRED"
    plans = (await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id == user_id,
        WealthPlanORM.status.in_(["DRAFT","AWAITING_CONFIRMATION"])))).all()
    for plan in plans:
        if plan.data.get("request",{}).get("account_id") == account_id:
            plan.status = "EXPIRED"


async def write(db: AsyncSession, user_id: str, action: str, body: dict) -> dict:
    """锁本人控制→账户；请求重放与范围修改同一事务。"""
    if not enabled():
        raise BankError("测试工具未启用", "DEMO_TOOLS_DISABLED", 403)
    await lock_profile(db,user_id)
    key,digest,previous = await receipt(db,user_id,action,body)
    if previous is not None:
        return previous
    if action not in {"reset","adjust","delete"}:
        raise BankError("测试动作不支持", "INVALID_REQUEST", 422)
    from .wealth_service import lock_accounts, stable_id
    if action in {"reset","adjust"}:
        ctx = await context_owned(db,user_id,body["run_id"])
        selection = await db.get(WealthContextSelectionORM,user_id)
        if selection is None or selection.context_id != ctx.context_id:
            raise BankError("只能处理当前测试轮次", "STALE_CONTEXT", 409)
        run_scope=ctx.data.get('run_id',ctx.context_id)
        peers=(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==user_id))).all()
        peers=[peer for peer in peers if peer.data.get('disposable') is True and
            peer.data.get('run_id',peer.context_id)==run_scope]
        await lock_accounts(db,[peer.account_id for peer in peers] if action=='reset' else [ctx.account_id]); await db.refresh(ctx)
        if ctx.version != body["expected_version"]:
            raise BankError("测试轮次已变化，请刷新", "STALE_CONTEXT", 409)
        account = await db.get(AccountORM,ctx.account_id,populate_existing=True)
        await invalidate(db,user_id,ctx.account_id)
        if action == "adjust":
            try: amount = exact_fen(body["amount"])
            except (ValueError, TypeError, ArithmeticError) as error:
                raise BankError("金额必须为正数且最多两位小数", "INVALID_AMOUNT", 422) from error
            direction=body["action"]
            if amount <= 0 or amount > 2_000_000_000 or direction not in {"credit","debit"}:
                raise BankError("测试资金金额或方向不合法", "INVALID_AMOUNT", 422)
            if direction == "debit" and amount > await available_cash(db,ctx.account_id,account.balance_fen):
                raise BankError("减少金额不能超过可用现金", "INSUFFICIENT_AVAILABLE_FUNDS", 409)
            if direction == "credit" and account.balance_fen + amount > 2_000_000_000:
                raise BankError("超过银行金额类型上限", "INVALID_AMOUNT", 422)
            account.balance_fen += amount if direction == "credit" else -amount
            ctx.version += 1
            db.add(TransactionORM(tx_id=stable_id("DFA",key),account_id=ctx.account_id,operation_id=stable_id("DA",key),
                type="simulation_credit" if direction == "credit" else "simulation_debit",amount_fen=amount,
                counterparty="模拟测试资金工具",category="other",note="可处置轮次测试调整，不是真实转账",timestamp=utc_now(),status="completed",channel="app"))
            result={"run_id":ctx.context_id,"epoch":ctx.version,"balance_fen":account.balance_fen,"amount_fen":amount,"action":direction}
        else:
            rid="RUN"+uuid4().hex[:28]; aid="SA"+uuid4().hex[:28]
            for peer in peers:
                await db.refresh(peer)
                await invalidate(db,user_id,peer.account_id)
                peer.version+=1; peer.data={**peer.data,'run_state':'ARCHIVED','archived_at':utc_iso(utc_now())}
            tasks=(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id==user_id,
                WealthTaskORM.account_id.in_([peer.account_id for peer in peers]), WealthTaskORM.status.not_in(['COMPLETED','CANCELLED','FAILED'])))).all()
            for task in tasks:
                task.status='PAUSED'; task.waiting_reason='ARCHIVED_RUN'; task.version+=1
            base=ctx.data.get("fixture_baseline",{})
            business_at=base.get("business_at") or ("2026-09-30T19:00:00+08:00" if ctx.data.get("mode")=="synthetic" else utc_iso(utc_now()))
            db.add(AccountORM(account_id=aid,user_id=user_id,account_type="savings",card_number="模拟测试****"+aid[-4:],
                balance_fen=2000000,daily_transfer_limit_fen=5000000,daily_transfer_used_fen=0,status="active")); await db.flush()
            db.add(WealthContextORM(context_id=rid,user_id=user_id,account_id=aid,dataset_key=ctx.dataset_key,version=1,
                data={"label":ctx.data.get("label","虚构测试轮次"),"mode":ctx.data.get("mode"),"scenario":ctx.data.get("scenario"),
                    "fixture":ctx.data.get("fixture",{}),"disposable":True,"run_state":"ACTIVE","business_at":business_at,
                    "fixture_version":ctx.data.get("fixture_version",ctx.dataset_key),"fixture_baseline":{"cash_fen":2000000,"business_at":business_at},"run_id":rid}))
            selection.context_id=rid; selection.version+=1
            result={"archived_run_id":ctx.context_id,'archived_run_ids':[peer.context_id for peer in peers],"run_id":rid,"account_id":aid,"epoch":1,"balance_fen":2000000,"selection_version":selection.version}
    else:
        result=await delete_archives(db,user_id,body)
    db.add(WealthDemoReceiptORM(receipt_id=key,user_id=user_id,digest=digest,action=action,result=result,created_at=utc_now()))
    record_bank_event(db,event_key=key,subject_user_id=user_id,action="wealth_demo_"+action,result="simulated",facts=result,
        decision_chain=["authenticated_owner","explicit_demo_switch","disposable_scope_locked","request_id_and_version_checked"])
    await db.commit()
    return result


async def archive_dependencies(db: AsyncSession, ctx: WealthContextORM) -> dict:
    """按FK及原命令账户定位独占记录，不按前缀猜范围。"""
    aid=ctx.account_id
    orders=(await db.scalars(select(WealthOrderORM).where(WealthOrderORM.account_id==aid))).all()
    operation_ids=[o.operation_id for o in orders]
    lots=(await db.scalars(select(WealthLotORM).where(WealthLotORM.account_id==aid))).all()
    auths=[a for a in (await db.scalars(select(WealthAuthorizationORM).where(WealthAuthorizationORM.user_id==ctx.user_id))).all() if a.command.get('account_id')==aid]
    plans=[p for p in (await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id==ctx.user_id))).all() if p.data.get('request',{}).get('account_id')==aid]
    conversations=[c for c in (await db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id==ctx.user_id))).all() if c.data.get('context_id')==ctx.context_id]
    tasks=(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id==ctx.user_id,WealthTaskORM.account_id==aid))).all()
    return {'operations':operation_ids,'lots':[l.lot_id for l in lots],'auths':[a.authorization_id for a in auths],
        'plans':[p.plan_id for p in plans],'conversations':[c.session_id for c in conversations],
        'tasks':[task.task_id for task in tasks]}


async def archives(db: AsyncSession, user_id: str) -> dict:
    if not enabled():
        raise BankError('测试工具未启用', 'DEMO_TOOLS_DISABLED', 403)
    rows = (await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id == user_id))).all()
    return {'items': [{'run_id': row.context_id, 'expected_version': row.version,
        'archived_at': row.data.get('archived_at'), 'fixture_version': row.data.get('fixture_version', row.dataset_key)}
        for row in rows if row.data.get('disposable') is True and row.data.get('run_state') == 'ARCHIVED']}


async def deletion_preview(db: AsyncSession, user_id: str, items: list[dict]) -> dict:
    if not items or len(items) > 100 or len({r['run_id'] for r in items}) != len(items):
        raise BankError('请选择不同的归档轮次', 'INVALID_REQUEST', 422)
    rows = []
    for item in items:
        ctx = await context_owned(db, user_id, item['run_id'], archived=True)
        if ctx.version != item['expected_version']:
            raise BankError('归档版本已改变', 'STALE_CONTEXT', 409)
        dependencies = await archive_dependencies(db, ctx)
        rows.append({'run_id': ctx.context_id, 'expected_version': ctx.version,
            'counts': {key: len(value) for key, value in dependencies.items()}})
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'runs': rows, 'preview_digest': digest, 'irreversible': True}


async def delete_archives(db: AsyncSession, user_id: str, body: dict) -> dict:
    """提交中重新锁定范围；全部轮次安全才能开始物理删除。"""
    if body.get('confirmed') is not True:
        raise BankError('请先预览并本人明确确认删除','CONFIRMATION_REQUIRED',409)
    items=body.get('runs',[])
    if not items or len(items)>100 or len({r['run_id'] for r in items})!=len(items):
        raise BankError('请选择不同的归档轮次','INVALID_REQUEST',422)
    contexts=[await context_owned(db,user_id,item['run_id'],archived=True) for item in items]
    peers=(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==user_id))).all()
    selected_ids={ctx.context_id for ctx in contexts}
    scopes={ctx.data.get('run_id',ctx.context_id) for ctx in contexts}
    if any(peer.context_id not in selected_ids and peer.data.get('run_id',peer.context_id) in scopes for peer in peers):
        raise BankError('同轮次还有关联账户，请一起选择归档删除', 'DELETION_BLOCKED',409)
    from .wealth_service import lock_accounts
    await lock_accounts(db,[c.account_id for c in contexts])
    preview = await deletion_preview(db, user_id, items)
    if body.get('preview_digest') != preview['preview_digest']:
        raise BankError('删除范围已变化，请重新预览确认', 'STALE_DELETE_PREVIEW', 409)
    selection = await db.get(WealthContextSelectionORM, user_id)
    if selection and selection.context_id in {ctx.context_id for ctx in contexts}:
        raise BankError('当前轮次不可删除', 'DELETION_BLOCKED', 409)
    dependencies=[]
    for ctx,item in zip(contexts,items):
        await db.refresh(ctx)
        if ctx.version!=item['expected_version'] or ctx.data.get('run_state')!='ARCHIVED':
            raise BankError('归档版本已改变','STALE_CONTEXT',409)
        data=await archive_dependencies(db,ctx)
        nodes=(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id.in_(data['tasks'])))).all()
        if any(node.status in {'RUNNING','UNKNOWN'} or node.lease_until for node in nodes):
            raise BankError('任务仍有执行租约或未知提交，请先核对', 'DELETION_BLOCKED', 409)
        cross_task=await db.scalar(select(WealthTaskNodeORM.node_id).where(
            WealthTaskNodeORM.operation_id.in_(data['operations']),WealthTaskNodeORM.task_id.not_in(data['tasks'])))
        if cross_task:
            raise BankError('订单存在跨轮次任务引用', 'DELETION_BLOCKED', 409)
        # 旧版本切范围复制出的混合历史不能删除其他轮次；新会话严格绑定单一范围。
        for sid in data['conversations']:
            conv=await db.get(WealthConversationORM,sid)
            cross=await db.scalar(select(WealthTaskORM.task_id).where(WealthTaskORM.session_id==sid,
                WealthTaskORM.account_id!=ctx.account_id))
            if conv.data.get('history_archive') or cross:
                raise BankError('存在混合历史或跨范围对话引用，禁止清理', 'DELETION_BLOCKED', 409)
        observations=(await db.scalars(select(WealthExecutionORM).where(WealthExecutionORM.authorization_id.in_(data['auths'])))).all()
        if any(o.status in {'UNKNOWN','SUBMITTING'} for o in observations):
            raise BankError('存在未核对写入，先按原号核对','DELETION_BLOCKED',409)
        cross=await db.scalar(select(WealthUnitHoldORM.hold_id).where(WealthUnitHoldORM.lot_id.in_(data['lots']),WealthUnitHoldORM.operation_id.not_in(data['operations'])))
        if cross:
            raise BankError('存在跨范围份额引用，禁止清理','DELETION_BLOCKED',409)
        dependencies.append(data)
    # 先删除从属再删除账户；全程保留外键约束。
    for ctx,data in zip(contexts,dependencies):
        old_receipts=(await db.scalars(select(WealthDemoReceiptORM).where(
            WealthDemoReceiptORM.user_id==user_id,WealthDemoReceiptORM.action=='adjust'))).all()
        for old in old_receipts:
            if old.result.get('run_id')==ctx.context_id:
                old.result={'run_id':ctx.context_id,'gone':True}
        chats=(await db.scalars(select(WealthChatRequestORM).where(WealthChatRequestORM.user_id==user_id))).all()
        for chat in chats:
            if chat.response.get('session_id') in data['conversations']:
                await db.delete(chat)
        audits=(await db.scalars(select(AuditLogORM).where(AuditLogORM.user_id==user_id,
            AuditLogORM.action.like('investment_%')))).all()
        for audit in audits:
            if audit.params and (audit.params.get('operation_id') in data['operations'] or audit.params.get('authorization_id') in data['auths']):
                await db.delete(audit)
        adjustments=(await db.scalars(select(AuditLogORM).where(AuditLogORM.user_id==user_id,
            AuditLogORM.action=='wealth_demo_adjust'))).all()
        for audit in adjustments:
            if audit.params and audit.params.get('run_id')==ctx.context_id: await db.delete(audit)
        for cls,predicate in [
            (WealthTaskControlORM,WealthTaskControlORM.task_id.in_(data['tasks'])),
            (WealthTaskNodeORM,WealthTaskNodeORM.task_id.in_(data['tasks'])),
            (WealthTaskORM,WealthTaskORM.task_id.in_(data['tasks'])),
            (WealthVerificationORM,WealthVerificationORM.authorization_id.in_(data['auths'])),
            (WealthExecutionORM,WealthExecutionORM.authorization_id.in_(data['auths'])),
            (WealthAuthorizationORM,WealthAuthorizationORM.authorization_id.in_(data['auths'])),
            (WealthSettlementORM,WealthSettlementORM.operation_id.in_(data['operations'])),
            (WealthUnitHoldORM,WealthUnitHoldORM.operation_id.in_(data['operations'])),
            (WealthCashHoldORM,WealthCashHoldORM.account_id==ctx.account_id),
            (WealthLotORM,WealthLotORM.account_id==ctx.account_id),
            (WealthOrderORM,WealthOrderORM.account_id==ctx.account_id),
            (WealthPlanORM,WealthPlanORM.plan_id.in_(data['plans'])),
            (WealthConversationORM,WealthConversationORM.session_id.in_(data['conversations'])),
            (WealthQuickQuotaORM,WealthQuickQuotaORM.scope_id==ctx.data.get('run_id',ctx.context_id)),
            (WealthLedgerReviewORM,WealthLedgerReviewORM.account_id==ctx.account_id),
            (TransactionORM,TransactionORM.account_id==ctx.account_id)]:
            await db.execute(delete(cls).where(predicate))
        await db.execute(delete(WealthContextORM).where(WealthContextORM.context_id==ctx.context_id))
        await db.execute(delete(AccountORM).where(AccountORM.account_id==ctx.account_id))
    return {'deleted_run_ids':[c.context_id for c in contexts],
        'deleted_account_ids':[c.account_id for c in contexts], 'deleted_count':len(contexts),
        'deleted_operation_ids':[operation for data in dependencies for operation in data['operations']],
        'deleted_task_ids':[task for data in dependencies for task in data['tasks']],
        'physical_delete':True,'occurred_at':utc_iso(utc_now())}
