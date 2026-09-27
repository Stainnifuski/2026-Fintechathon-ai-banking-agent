"""银行拥有的可重复升级步骤。"""

from sqlalchemy import select

from ...domain.phone import normalize_phone
from ...models.user import UserORM
from ...models.user_phone_registry import UserPhoneRegistryORM


async def upgrade_phone_registry(session_factory) -> None:
    """检查旧库号码冲突并填充唯一登记表；不改写客户数据。"""
    async with session_factory() as session:
        users = (await session.execute(select(UserORM))).scalars().all()
        rows = (await session.execute(select(UserPhoneRegistryORM))).scalars().all()
        by_phone = {row.canonical_phone: row.user_id for row in rows}
        by_user = {row.user_id: row.canonical_phone for row in rows}
        for user in users:
            try:
                canonical = normalize_phone(user.phone)
            except ValueError as error:
                raise RuntimeError(
                    f"旧数据库用户 {user.user_id} 的手机号格式无效；升级已停止"
                ) from error
            if (canonical in by_phone and by_phone[canonical] != user.user_id) or (
                user.user_id in by_user and by_user[user.user_id] != canonical
            ):
                raise RuntimeError(
                    f"旧数据库手机号映射冲突（用户 {user.user_id}）；升级已停止"
                )
            if canonical not in by_phone:
                session.add(UserPhoneRegistryORM(
                    canonical_phone=canonical, user_id=user.user_id
                ))
                by_phone[canonical] = user.user_id
                by_user[user.user_id] = canonical
        await session.commit()
