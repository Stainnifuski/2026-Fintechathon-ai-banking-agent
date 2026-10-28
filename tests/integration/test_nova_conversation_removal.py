"""Conversation removal must respect real foreign keys and retain bank facts."""
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Base, UserORM, AccountORM, TransactionORM
from backend.app.models.nova_agent_runtime import NovaAgentRuntimeORM
from backend.app.models.nova_conversation import NovaConversationORM
from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user
from backend.app.routers.bank_conversations import router
from backend.app.services.bank import nova_conversations


@pytest.mark.asyncio
@pytest.mark.parametrize('with_runtime', [False, True])
async def test_remove_conversation_with_foreign_keys_preserves_other_history_and_bank(with_runtime):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(UserORM(user_id='U1', name='owner', phone='13800000001', id_card='test-owner', status='active'))
            await db.commit()
            db.add(AccountORM(account_id='A1', user_id='U1', account_type='savings', card_number='test-card', balance_fen=10000))
            await db.commit()
            db.add(TransactionORM(tx_id='TX1', account_id='A1', type='salary', amount_fen=10000,
                                  counterparty='test', category='salary', status='completed'))
            await db.commit()
            target = (await nova_conversations.create(db, 'U1'))['session_id']
            other = (await nova_conversations.create(db, 'U1'))['session_id']
            db.add(NovaAgentRuntimeORM(conversation_id=other, user_id='U1', state={'keep': True}))
            if with_runtime:
                db.add(NovaAgentRuntimeORM(conversation_id=target, user_id='U1', state={'version': 1}))
            await db.commit()
            app = FastAPI()
            app.include_router(router)
            owner = {'user_id': 'U2'}

            async def current_user():
                return owner

            async def current_db():
                yield db

            app.dependency_overrides[get_current_user] = current_user
            app.dependency_overrides[get_db] = current_db
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.delete(f'/api/v1/bank/nova/conversations/{target}')).status_code == 404
                owner['user_id'] = 'U1'
                response = await client.delete(f'/api/v1/bank/nova/conversations/{target}')
                assert response.status_code == 204, response.text
            assert await db.get(NovaConversationORM, target) is None
            assert await db.get(NovaAgentRuntimeORM, target) is None
            assert await db.get(NovaConversationORM, other) is not None
            assert (await db.get(NovaAgentRuntimeORM, other)).state == {'keep': True}
            assert (await db.get(AccountORM, 'A1')).balance_fen == 10000
            assert (await db.get(TransactionORM, 'TX1')).amount_fen == 10000
    finally:
        await engine.dispose()
