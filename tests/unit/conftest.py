"""NOVA endpoint units use a disposable bank store for persisted chat history."""
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest_asyncio.fixture(autouse=True)
async def nova_endpoint_store(request, monkeypatch):
    if request.node.path.name not in {'test_nova.py', 'test_nova_stream.py'}:
        yield
        return
    from backend.app.models import Base, UserORM
    from backend.app.database import connection
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(UserORM(user_id='U001', name='unit-user', phone='13800000001', id_card='unit-only', status='active'))
        await db.commit()
    monkeypatch.setattr(connection, 'AsyncSessionLocal', sessions)
    try:
        yield
    finally:
        await engine.dispose()
