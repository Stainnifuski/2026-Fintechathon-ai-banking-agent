"""默认演示联系人数据。"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, ContactORM, UserORM
from backend.app.services.bank import seed_service
from backend.app.security.passwords import verify_password


@pytest.mark.asyncio
async def test_seed_has_five_contacts_with_consecutive_phones(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_service, "AsyncSessionLocal", sessions)

    await seed_service.seed_bank_data()
    await seed_service.seed_bank_data()

    async with sessions() as session:
        users = (
            await session.execute(select(UserORM).order_by(UserORM.user_id))
        ).scalars().all()
        contacts = (
            await session.execute(
                select(ContactORM)
                .where(ContactORM.user_id == "U001")
                .order_by(ContactORM.contact_id)
            )
        ).scalars().all()
        account_count = await session.scalar(select(func.count()).select_from(AccountORM))
    assert [user.phone for user in users] == [
        "13800000001",
        "13800000002",
        "13800000003",
        "13800000004",
        "13800000005",
        "13800000006",
    ]
    assert len(contacts) == 5
    assert [contact.phone for contact in contacts] == [
        "13800000002",
        "13800000003",
        "13800000004",
        "13800000005",
        "13800000006",
    ]
    assert account_count == 7
    assert all(verify_password("password123", user.password_hash) for user in users)
    await engine.dispose()
