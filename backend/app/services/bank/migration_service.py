"""银行拥有的可重复升级步骤。"""

from sqlalchemy import inspect, select, text

from ...domain.phone import normalize_phone
from ...models.user import UserORM
from ...models.user_phone_registry import UserPhoneRegistryORM
from ...models.payment_request import PaymentRequestORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.transaction import TransactionORM
from ...domain.money import exact_fen, decimal_yuan, ledger_fen

MONEY_COLUMNS = {
    "accounts": ("account_id", ("balance", "daily_transfer_used", "daily_transfer_limit")),
    "transactions": ("tx_id", ("amount",)),
}


async def migrate_bank_money(connection, *, rollback: bool = False) -> None:
    """整批预检后原子迁移金额列；rollback从当前整数账本还原元列。"""
    dialect = connection.dialect.name
    if dialect == "sqlite":
        # sqlite驱动默认不会为DDL开启事务，显式开始确保DDL也可回滚。
        active = await connection.run_sync(lambda conn: conn.connection.driver_connection.in_transaction)
        if not active:
            await connection.execute(text("BEGIN IMMEDIATE"))
    if dialect == "postgresql":
        await connection.execute(text("LOCK TABLE accounts, transactions IN ACCESS EXCLUSIVE MODE"))

    plans = []
    for table, (key, names) in MONEY_COLUMNS.items():
        columns = await connection.run_sync(lambda conn, table=table: {
            col["name"] for col in inspect(conn).get_columns(table)
        })
        source_names = [f"{name}_fen" if rollback else name for name in names]
        target_names = [name if rollback else f"{name}_fen" for name in names]
        if not set(source_names) & columns:
            if not set(target_names) <= columns:
                raise RuntimeError(f"{table}金额列不完整，停止升级")
            continue
        if not set(source_names) <= columns or set(target_names) & columns:
            raise RuntimeError(f"{table}存在混合金额结构，停止升级")
        rows = (await connection.execute(text(
            f"SELECT {key}, {', '.join(source_names)} FROM {table}"
        ))).mappings().all()
        converted = []
        for row in rows:
            values = {"row_key": row[key]}
            for source, target in zip(source_names, target_names):
                try:
                    if rollback:
                        fen = ledger_fen(row[source])
                        value = float(decimal_yuan(fen))
                        if exact_fen(value) != fen:
                            raise ValueError("旧浮点结构无法精确表达当前账目")
                    else:
                        value = exact_fen(row[source])
                        if table == "transactions" and value == 0:
                            raise ValueError("流水金额必须大于零")
                except ValueError as error:
                    raise RuntimeError(f"{table}/{row[key]}/{source}金额异常，停止迁移") from error
                values[target] = value
            converted.append(values)
        plans.append((table, key, source_names, target_names, converted))

    # 所有表均检查通过之后才改列，不舍入或删除异常数据。
    for table, key, sources, targets, rows in plans:
        for target in targets:
            minimum = 1 if table == "transactions" else 0
            definition = "DOUBLE PRECISION NOT NULL DEFAULT 0" if rollback else (
                f"BIGINT NOT NULL DEFAULT {minimum} CHECK ({target} >= {minimum} AND {target} = CAST({target} AS BIGINT))"
            )
            await connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {target} {definition}"))
        if rows:
            assignments = ', '.join(f"{name} = :{name}" for name in targets)
            await connection.execute(text(f"UPDATE {table} SET {assignments} WHERE {key} = :row_key"), rows)
        for source in sources:
            await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {source}"))


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


async def upgrade_card_columns(connection) -> None:
    """补齐卡片额度、有效期与渠道开关列；create_all 不会升级旧 cards 表。"""
    def add_missing(sync_connection) -> None:
        inspector = inspect(sync_connection)
        if "cards" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("cards")}
        definitions = {
            "used_credit_fen": "BIGINT NOT NULL DEFAULT 0",
            "valid_thru": "VARCHAR(7)",
            "online_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
            "atm_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
            "overseas_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
            "contactless_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
            "replaced_by_card_id": "VARCHAR(32)",
            "cancelled_at": "TIMESTAMP WITH TIME ZONE",
        }
        for name, definition in definitions.items():
            if name not in columns:
                sync_connection.execute(text(f"ALTER TABLE cards ADD COLUMN {name} {definition}"))

    await connection.run_sync(add_missing)


async def upgrade_contact_knowledge_columns(connection) -> None:
    """旧联系人来源不明，默认不向前端公开任何号码。"""
    def add_missing(sync_connection) -> None:
        inspector = inspect(sync_connection)
        if "contacts" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("contacts")}
        for name in ("phone_known", "card_known"):
            if name not in columns:
                sync_connection.execute(text(
                    f"ALTER TABLE contacts ADD COLUMN {name} BOOLEAN NOT NULL DEFAULT FALSE"
                ))

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


async def upgrade_flower_purchase_transactions(session_factory) -> None:
    """把早期花店付款的普通转账流水幂等升级为消费与商户收款。"""
    async with session_factory() as session:
        merchant = await session.get(UserORM, "U007")
        if merchant is None:
            return
        payments = (
            await session.execute(
                select(PaymentRequestORM, PaymentRequestPaymentORM)
                .join(
                    PaymentRequestPaymentORM,
                    PaymentRequestPaymentORM.payment_request_id
                    == PaymentRequestORM.payment_request_id,
                )
                .where(
                    PaymentRequestORM.requester_user_id == "U007",
                    PaymentRequestORM.status == "PAID",
                )
            )
        ).all()
        changed = False
        for payment, link in payments:
            payer = await session.get(UserORM, payment.payer_user_id)
            outgoing = (
                await session.execute(
                    select(TransactionORM).where(
                        TransactionORM.operation_id == link.operation_id
                    )
                )
            ).scalar_one_or_none()
            if outgoing is not None and outgoing.type == "transfer_out":
                outgoing.type = "purchase"
                outgoing.counterparty = merchant.name
                outgoing.category = "shopping"
                changed = True
            incoming = (
                await session.execute(
                    select(TransactionORM).where(
                        TransactionORM.account_id == payment.receive_account_id,
                        TransactionORM.type == "transfer_in",
                        TransactionORM.amount_fen == payment.amount_fen,
                        TransactionORM.note == payment.note,
                    )
                )
            ).scalars().all()
            for transaction in incoming:
                transaction.type = "merchant_sale"
                transaction.counterparty = payer.name if payer else payment.payer_user_id
                transaction.category = "shopping"
                changed = True
        if changed:
            await session.commit()
