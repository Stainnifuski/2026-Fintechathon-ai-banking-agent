"""01只读context适配；不注册公共入口、不签发购买授权。"""
from typing import Protocol, Callable, Awaitable
from .wealth_portfolio_candidates import CandidateError

class PortfolioAccessSource(Protocol):
    async def resolve(self, user_id: str, context_id: str, *, write: bool, portfolio_id: str | None=None) -> dict: ...

def access_binding(grant: dict) -> dict:
    """只绑定身份/范围/真实切换与关联版本；编辑权限不是只读方案身份。"""
    fields = ('user_id','context_id','context_epoch','asset_scope_id','selection_version',
              'session_ref','related_task_version')
    return {**{key:grant[key] for key in fields if key in grant},'account_ids':sorted(grant['account_ids'])}

class ExistingPortfolioAccess:
    """由B注入全授权scope resolver；复用现有plan edit门禁并检查同context任务。"""
    def __init__(self, db, scope_resolver: Callable[[str,str],Awaitable[dict]]):
        self.db=db;self.scope_resolver=scope_resolver
    async def resolve(self,user_id: str,context_id: str,*,write: bool,portfolio_id: str | None=None) -> dict:
        from sqlalchemy import select
        from ...models.wealth import WealthTaskORM as Task
        from .wealth_task import require_plan_edit, TERMINAL, current_workspace_task
        grant=await self.scope_resolver(user_id,context_id)
        if write:
            if portfolio_id:
                from ...integrations.wealth_authorization import WealthPolicyError
                try:await require_plan_edit(self.db,user_id,portfolio_id)
                except WealthPolicyError as error:raise CandidateError(error.code,error.status_code) from error
            tasks=(await self.db.scalars(select(Task).where(Task.user_id==user_id,Task.context_id==context_id,Task.status.not_in(TERMINAL)))).all()
            if any(t.control_mode!='AGENT' or t.status=='PAUSED' or t.data.get('stop_new_submissions') or t.data.get('session_closed') for t in tasks if current_workspace_task(t)):
                raise CandidateError('TASK_WRITE_BLOCKED',409)
        return grant
