"""银行拥有的可重复升级步骤。"""

from sqlalchemy import inspect, select, text

from ...domain.phone import normalize_phone
from ...models.user import UserORM
from ...models.user_phone_registry import UserPhoneRegistryORM


async def upgrade_legacy_user_mfa_columns(connection) -> None:
    """补齐主分支新增的用户 TOTP 列；create_all 不会升级旧 users 表。"""
    def add_missing(sync_connection) -> None:
        inspector = inspect(sync_connection)
        if "users" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("users")}
        definitions = {
            "totp_secret": "VARCHAR(64)",
            "totp_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
            "totp_last_timestep": "BIGINT",
        }
        for name, definition in definitions.items():
            if name not in columns:
                sync_connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))

    await connection.run_sync(add_missing)


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
