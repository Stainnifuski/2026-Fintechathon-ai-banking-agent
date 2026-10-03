"""备份副本先行的历史核对：证据足够才修复瞬时，不推测价格/余额。"""
import argparse
import asyncio
import json
import os
from datetime import timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ...domain.time import BEIJING, as_utc, utc_iso, utc_now
from ...models.account import AccountORM
from ...models.audit_log import AuditLogORM
from ...models.transaction import TransactionORM
from ...models.wealth import WealthLedgerReviewORM, WealthOrderORM, WealthSettlementORM, WealthCashHoldORM, WealthLotORM
from ...models.wealth_research import WealthContextORM
from .transfer_service import BankError


async def require_reconciled(db: AsyncSession, account_id: str) -> None:
    """未能证明历史资金有效时停新写入，查询和原号终态核对保留。"""
    review = await db.scalar(select(WealthLedgerReviewORM.review_id).where(
        WealthLedgerReviewORM.account_id == account_id,
        WealthLedgerReviewORM.status == "RECONCILIATION_REQUIRED"))
    if review:
        raise BankError("该账户历史模拟计价需要核对，已保留账务；暂不允许新增交易", "LEDGER_RECONCILIATION_REQUIRED", 409)


async def reconcile(db: AsyncSession, *, apply: bool = False) -> dict:
    """dry-run逐笔引用同事务银行审计；普通未来计价不能仅改日期放行。"""
    from .wealth_service import stable_id, lock_accounts
    contexts = {c.account_id: c for c in (await db.scalars(select(WealthContextORM))).all()}
    orders = (await db.scalars(select(WealthOrderORM).order_by(WealthOrderORM.operation_id))).all()
    if apply:
        await lock_accounts(db, [o.account_id for o in orders])
    audits = (await db.scalars(select(AuditLogORM).where(
        AuditLogORM.action.in_(["investment_pricing", "investment_cash", "investment_accept", "investment_quick_cash"])))).all()
    events = {(a.action, a.params.get("operation_id")): a for a in audits}
    rows: list[dict] = []
    summaries: dict[str, dict] = {}
    ledger_links = []
    acceptance_times = []
    for order in orders:
        sandbox = contexts.get(order.account_id)
        scope = "sandbox" if sandbox and sandbox.data.get("disposable") is True else "ordinary"
        accepted_audit = events.get(('investment_accept', order.operation_id))
        acceptance_key = 'accept-time-v1:' + order.operation_id
        previous_acceptance = await db.get(WealthLedgerReviewORM, acceptance_key)
        if previous_acceptance:
            acceptance_times.append({**previous_acceptance.evidence, 'status': previous_acceptance.status,
                                     'already_recorded': True})
        elif accepted_audit:
            audit_at = as_utc(accepted_audit.timestamp, legacy_utc=True)
            accepted_at = as_utc(order.accepted_at, legacy_utc=True)
            if abs((accepted_at-audit_at).total_seconds()) > 2:
                proven = (accepted_audit.user_id == order.user_id and accepted_audit.result == 'success'
                          and accepted_audit.params.get('digest') == order.param_digest
                          and accepted_audit.params.get('version_id') == order.version_id)
                status = 'TIME_REPAIRED' if scope == 'sandbox' and proven else 'RECONCILIATION_REQUIRED'
                evidence = {'operation_id': order.operation_id, 'account_id': order.account_id, 'scope': scope,
                    'old_accepted_at': order.accepted_at.isoformat(), 'audit_id': accepted_audit.audit_id,
                    'recorded_event_utc': utc_iso(audit_at), 'evidence_verified': proven,
                    'changed_money_or_units': False, 'reason': 'bound_bank_accept_audit; preserve_business_dates'}
                acceptance_times.append({**evidence, 'status': status, 'already_recorded': False})
                if apply:
                    if proven: order.accepted_at = audit_at
                    db.add(WealthLedgerReviewORM(review_id=acceptance_key, account_id=order.account_id,
                        status=status, evidence=evidence, created_at=utc_now()))
        elif as_utc(order.accepted_at, legacy_utc=True) > utc_now():
            evidence = {'operation_id': order.operation_id, 'account_id': order.account_id, 'scope': scope,
                'old_accepted_at': order.accepted_at.isoformat(), 'audit_id': None,
                'recorded_event_utc': None, 'evidence_verified': False, 'changed_money_or_units': False,
                'reason': 'future_acceptance_without_bank_audit; preserve_and_restrict'}
            acceptance_times.append({**evidence, 'status': 'RECONCILIATION_REQUIRED', 'already_recorded': False})
            if apply:
                db.add(WealthLedgerReviewORM(review_id=acceptance_key, account_id=order.account_id,
                    status='RECONCILIATION_REQUIRED', evidence=evidence, created_at=utc_now()))
        for kind, prefix, action in [("pricing", "WPT", "investment_pricing"), ("cash", "WRT", "investment_cash")]:
            if kind == "pricing" and order.direction != "purchase":
                continue
            tx = await db.get(TransactionORM, stable_id(prefix, order.operation_id))
            if tx is None:
                continue
            if tx.operation_id is None:
                actual=order.details.get('actual',{})
                expected=(actual.get('gross_fen',0)-actual.get('refund_fen',0)) if kind=='pricing' else actual.get('cash_fen')
                evidence_audit=events.get((action,order.operation_id)) or (events.get(('investment_quick_cash',order.operation_id)) if kind=='cash' else None)
                evidence_event=await db.get(WealthSettlementORM,order.operation_id+(':pricing' if kind=='pricing' else ':cash'))
                if kind=='cash' and evidence_event is None:
                    evidence_event=await db.get(WealthSettlementORM,order.operation_id+':quick')
                collision=await db.scalar(select(TransactionORM.tx_id).where(TransactionORM.operation_id==order.operation_id))
                linked=bool(tx.account_id==order.account_id and tx.amount_fen==expected and evidence_event and evidence_audit and not collision)
                ledger_links.append({'tx_id':tx.tx_id,'operation_id':order.operation_id,'evidence_verified':linked,'changed_money_or_units':False})
                if apply and linked: tx.operation_id=order.operation_id
            key = "time-v1:" + tx.tx_id
            old_review = await db.get(WealthLedgerReviewORM, key)
            if old_review:
                rows.append({**old_review.evidence, "status": old_review.status, "already_recorded": True})
                continue
            audit = events.get((action, order.operation_id)) or (events.get(('investment_quick_cash',order.operation_id)) if kind=='cash' else None)
            event_time = as_utc(audit.timestamp, legacy_utc=True) if audit else None
            # 旧实现将business_now直接写入SQLite；不把旧naive误当UTC。
            old_timestamp = tx.timestamp.isoformat()
            future_pricing = bool(event_time and order.pricing_date > event_time.astimezone(BEIJING).date())
            current_time = as_utc(tx.timestamp, legacy_utc=True)
            if not future_pricing and event_time and abs((current_time-event_time).total_seconds()) < 2:
                continue
            status = "TIME_REPAIRED" if scope == "sandbox" and event_time else "RECONCILIATION_REQUIRED"
            evidence = {"tx_id": tx.tx_id, "account_id": tx.account_id, "operation_id": order.operation_id,
                "scope": scope, "amount_fen": tx.amount_fen, "direction": order.direction,
                "old_timestamp": old_timestamp, "pricing_date": order.pricing_date.isoformat(),
                "audit_id": audit.audit_id if audit else None,
                "recorded_event_utc": utc_iso(event_time) if event_time else None,
                "future_pricing_at_recording": future_pricing,
                "reason": "same_transaction_bank_audit; sandbox_business_clock" if status == "TIME_REPAIRED" else "ordinary_accelerated_pricing_or_missing_evidence; preserve_financial_facts",
                "proposed_action": "restore_event_instant_preserve_business_time" if status == "TIME_REPAIRED" else "restore_proven_event_instant_and_restrict_new_writes" if event_time else "restrict_new_writes_for_review"}
            rows.append({**evidence, "status": status, "already_recorded": False})
            if apply:
                if event_time:
                    tx.timestamp = event_time
                    settlement = await db.get(WealthSettlementORM, order.operation_id + ":" + kind)
                    if settlement:
                        settlement.data = {**settlement.data, "legacy_business_timestamp": settlement.created_at.isoformat(), "time_evidence_audit_id": audit.audit_id}
                        settlement.created_at = event_time
                db.add(WealthLedgerReviewORM(review_id=key, account_id=tx.account_id, status=status,
                    evidence=evidence, created_at=utc_now()))
        account = await db.get(AccountORM, order.account_id)
        held = await db.scalar(select(func.coalesce(func.sum(WealthCashHoldORM.amount_fen), 0)).where(
            WealthCashHoldORM.account_id == order.account_id, WealthCashHoldORM.status == 'HELD'))
        lots = (await db.scalars(select(WealthLotORM).where(WealthLotORM.account_id == order.account_id))).all()
        account_orders = [item for item in orders if item.account_id == order.account_id]
        summaries[order.account_id] = {"scope": scope, "balance_fen": account.balance_fen,
            'held_fen': held, 'available_fen': account.balance_fen-held,
            'remaining_units_scaled': sum(item.units_scaled for item in lots),
            'remaining_cost_fen': sum(item.cost_fen for item in lots),
            'receivable_fen': sum(item.details.get('actual', {}).get('cash_fen', 0)
                for item in account_orders if item.status == 'PENDING_CASH'),
            'outstanding_orders': [{'operation_id':item.operation_id, 'status':item.status}
                for item in account_orders if item.status not in ('SETTLED','CANCELLED','REJECTED','FAILED')],
            "financial_fields_changed": False}
    if apply:
        await db.commit()
    return {"mode": "apply" if apply else "dry_run", "rows": rows, "accounts": summaries, "ledger_links":ledger_links,
        "acceptance_times": acceptance_times,
        "changed_money_or_units": False, "recorded_at": utc_iso(utc_now())}


async def main(apply: bool) -> None:
    """目标必须显式指定且事先备份；输出无手机号、密码和密钥。"""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("请显式设置已备份的DATABASE_URL")
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            print(json.dumps(await reconcile(db, apply=apply), ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="理财历史核对；默认只读dry-run，先备份和副本验证")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
