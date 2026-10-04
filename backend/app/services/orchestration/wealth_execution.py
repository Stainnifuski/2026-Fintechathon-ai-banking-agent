"""5号原号恢复与未知提交持久化，无银行ORM依赖。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...integrations.bank_api import BankApiError, BankResponseUnknown
from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...integrations.wealth_authorization import WealthPolicyError, lock_profile
from ...models.wealth import WealthAuthorizationORM, WealthExecutionORM
from ...schemas.wealth import InvestmentCommand


async def record(db: AsyncSession, user_id: str, operation_id: str, authorization_id: str,
                 status: str, result: dict) -> None:
    """短事务记录观察；不靠此表判断账款可以释放。"""
    await lock_profile(db, user_id)
    row = await db.get(WealthExecutionORM, operation_id)
    if row is None:
        db.add(WealthExecutionORM(operation_id=operation_id, user_id=user_id,
            authorization_id=authorization_id, status=status, result=result))
    else:
        row.status, row.result = status, result
    await db.commit()


async def execute(db: AsyncSession, user_id: str, authorization_id: str, bank: HttpWealthBankApi) -> dict:
    """先核对原编号；查询404后只允许同号同参显式提交。"""
    row = await db.get(WealthAuthorizationORM, authorization_id)
    if row is None or row.user_id != user_id:
        raise WealthPolicyError("预览不存在", "NOT_FOUND", 404)
    command = InvestmentCommand.model_validate(row.command)
    if command.plan_id:
        raise WealthPolicyError("组合子单请从方案入口原子受理", "PLAN_EXECUTION_REQUIRED")
    await db.rollback()
    try:
        fact = await bank.query(user_id, command.operation_id)
    except BankResponseUnknown as error:
        await record(db, user_id, command.operation_id, authorization_id, "UNKNOWN", {"code": error.code})
        raise
    except BankApiError as error:
        if error.status_code != 404:
            raise
        await record(db, user_id, command.operation_id, authorization_id, "SUBMITTING", {})
        try:
            fact = await bank.submit(user_id, command, authorization_id)
        except BankResponseUnknown as unknown:
            await record(db, user_id, command.operation_id, authorization_id, "UNKNOWN", {"code": unknown.code})
            raise
        except BankApiError as rejected:
            await record(db, user_id, command.operation_id, authorization_id, "REJECTED", {"code": rejected.code})
            raise
    await record(db, user_id, command.operation_id, authorization_id, fact["status"], fact)
    from .wealth_lifecycle import prune_tasks
    await prune_tasks(db,user_id);await db.commit()
    return fact


async def observations(db: AsyncSession, user_id: str) -> list[dict]:
    """刷新后恢复应用观察，不替代银行事实。"""
    rows = (await db.scalars(select(WealthExecutionORM).where(WealthExecutionORM.user_id == user_id))).all()
    return [{"operation_id": row.operation_id, "authorization_id": row.authorization_id,
             "status": row.status, "result": row.result} for row in rows]
