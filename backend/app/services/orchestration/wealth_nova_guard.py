"""Block authority changes while a NOVA rollback awaits the owner's decision."""
from sqlalchemy import select
from ...models.wealth import WealthConversationORM
from ...integrations.wealth_authorization import WealthPolicyError


async def require_no_pending_backtrack(db,user_id,plan_id):
    if not plan_id:return
    rows=await db.scalars(select(WealthConversationORM).where(
        WealthConversationORM.user_id==user_id,WealthConversationORM.session_id.like('NW%')).execution_options(populate_existing=True))
    if any(r.data.get('execution_plan_id')==plan_id and r.data.get('pending_backtrack') for r in rows):
        raise WealthPolicyError('请先在卡片确认或取消回退，当前不会确认、验证或提交交易','BACKTRACK_PENDING',409)
