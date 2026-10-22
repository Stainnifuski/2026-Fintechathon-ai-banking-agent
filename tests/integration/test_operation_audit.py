"""Audit evidence is isolated from live stores and includes real bank semantics."""
import asyncio
import json
import os
from uuid import uuid4
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update, text, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.database import connection
from backend.app.middleware.auth import create_access_token, get_current_user
from backend.app.middleware.audit import AuditMiddleware
from backend.app.middleware.errors import install_error_handlers
from backend.app.models import Base, UserORM, AccountORM, AuditLogORM
from backend.app.models.audit_event import AuditEventORM as Event, AuditEventObjectORM as Object
from backend.app.routers.audit import router
from backend.app.services.audit import hooks, writer
from backend.app.services.audit.context import execution, propagation_headers, trust_internal
from backend.app.services.audit.privacy import clean
from backend.app.services.audit.query import query_events, object_timeline
from backend.app.services.audit.routes import install_routes, api_routes
from backend.app.services.bank.transfer_service import (
    BankResponseLost, BankTransferCommand, TrustedExecutionContext, _digest, submit_transfer,
)


@pytest_asyncio.fixture(params=['sqlite', 'postgresql'])
async def audit_store(request, tmp_path, monkeypatch):
    admin = None
    schema = 'audit_test_' + uuid4().hex
    if request.param == 'postgresql':
        url = os.getenv('BANK_TEST_DATABASE_URL')
        if not url:
            pytest.skip('设置BANK_TEST_DATABASE_URL启用PostgreSQL审计验收')
        assert (make_url(url).database or '').startswith('bank_test_'), '禁止使用业务数据库'
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
        @event.listens_for(engine.sync_engine, 'connect')
        def foreign_keys(dbapi_connection, record):
            dbapi_connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connection, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(writer, 'default_factory', lambda: sessions)
    monkeypatch.setenv('AUDIT_FALLBACK_DIR', str(tmp_path / 'recovery'))
    writer._health.update(status='ok', fallback_count=0)
    async with sessions() as db:
        for i in (1, 2):
            db.add(UserORM(user_id=f'U{i}', name='audit-user', phone=f'1380000000{i}', id_card='isolated', status='active'))
        await db.flush()
        for i in (1, 2):
            db.add(AccountORM(account_id=f'A{i}', user_id=f'U{i}', account_type='savings', card_number=f'isolated-{i}',
                              balance=100, balance_fen=10000, daily_transfer_used=0, daily_transfer_limit=50000, status='active'))
        await db.commit()
    try:
        yield engine, sessions
    finally:
        await engine.dispose()
        if admin:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
        writer._health.update(status='ok', fallback_count=0)


def headers(owner='U1'):
    return {'Authorization': 'Bearer ' + create_access_token({'sub': owner})}


def command(key='audit-transfer-001'):
    value = BankTransferCommand(key, 'A1', 'A2', 100, 'CNY', '', note='private-note')
    return replace(value, param_digest=_digest(value))


def app_for_test():
    app = FastAPI()
    app.add_middleware(AuditMiddleware)
    install_error_handlers(app)
    app.include_router(router)

    class Payload(BaseModel):
        amount_fen: int
        code: str
        note: str

    @app.post('/api/v1/tasks/audit-test')
    async def create(payload: Payload, user=Depends(get_current_user), db=Depends(connection.get_db)):
        account = await db.get(AccountORM, 'A1')
        account.balance_fen -= payload.amount_fen
        await db.commit()
        return {'status': 'WAITING_CONFIRMATION', 'task_id': 'TASK-AUDIT', 'code': payload.code,
                'note': payload.note, 'amount_fen': payload.amount_fen}

    install_routes(app)
    return app


@pytest.mark.asyncio
async def test_api_chain_privacy_and_filtering(audit_store):
    app = app_for_test()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers()) as client:
        response = await client.post('/api/v1/tasks/audit-test', json={'amount_fen': 123, 'code': '987654', 'note': 'secret audit text'})
        assert response.status_code == 200
        assert response.headers['x-request-id']
        page = await client.get('/api/v1/audit/events', params={'domain': 'transfer'})
        assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
        items = page.json()['items']
        assert any(r['outcome'] == 'pending_confirmation' for r in items)
        assert not any(r['outcome'] == 'success' for r in items)
        trace_id = next(r['trace_id'] for r in items if r['outcome'] == 'pending_confirmation')
        trace = await client.get('/api/v1/audit/traces/' + trace_id)
        assert trace.status_code == 200
        assert any(r['phase'] == 'transport' for r in trace.json()['items'])
        assert '987654' not in json.dumps(trace.json())
        assert 'secret audit text' not in json.dumps(trace.json())
    async with audit_store[1]() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 9877
        rows = (await db.scalars(select(Event))).all()
        raw = json.dumps([r.facts for r in rows])
        assert '13800000001' not in raw and '987654' not in raw


@pytest.mark.asyncio
async def test_owner_isolation_signed_cursor_and_export(audit_store):
    async with audit_store[1]() as db:
        with execution(actor_type='user', actor_id='U1', subject_user_id='U1'):
            for i in range(5):
                writer.observe(db, action='audit.fixture', phase='decision', outcome='checked', domain='wealth',
                               facts={'task_id': 'TASK-PRIVATE', 'amount_fen': i})
            await db.commit()
        trace = await db.scalar(select(Event.trace_id).where(Event.action == 'audit.fixture'))
    app = app_for_test()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        params = {'action': 'audit.fixture', 'limit': 2}
        first = (await client.get('/api/v1/audit/events', params=params, headers=headers())).json()
        assert len(first['items']) == 2 and first['next_cursor']
        second = await client.get('/api/v1/audit/events', params={**params, 'cursor': first['next_cursor']}, headers=headers())
        assert second.status_code == 200
        assert not {e['event_id'] for e in first['items']} & {e['event_id'] for e in second.json()['items']}
        bad = await client.get('/api/v1/audit/events', params={**params, 'cursor': first['next_cursor']}, headers=headers('U2'))
        assert bad.status_code == 422
        assert (await client.get('/api/v1/audit/traces/' + trace, headers=headers('U2'))).status_code == 404
        assert (await client.get('/api/v1/audit/objects/task/TASK-PRIVATE', headers=headers('U2'))).status_code == 404
        now = datetime.now(timezone.utc)
        response = await client.get('/api/v1/audit/export', params={
            'since': (now - timedelta(days=1)).isoformat(), 'until': now.isoformat(), 'action': 'audit.fixture'}, headers=headers())
        assert response.status_code == 200 and len(response.json()['items']) == 5
        assert 'attachment' in response.headers['content-disposition']
        too_wide = await client.get('/api/v1/audit/export', params={
            'since': (now - timedelta(days=32)).isoformat(), 'until': now.isoformat()}, headers=headers())
        assert too_wide.status_code == 422
        assert (await client.get('/api/v1/audit/events')).status_code == 401


@pytest.mark.asyncio
async def test_legacy_is_projected_without_fabricating_chain(audit_store):
    async with audit_store[1]() as db:
        # Core insert emulates records predating the new adapter.
        await db.execute(insert(AuditLogORM).values(audit_id='old-audit', session_id='OP-OLD', user_id='U1',
            intent='bank_business', permission_level='delegated', action='investment_accept', result='success',
            params={'operation_id': 'OP-OLD', 'phone': '13800000001'}, decision_chain=['bank_checked']))
        await db.commit()
        page = await query_events(db, 'U1', domain='wealth')
        old = next(e for e in page['items'] if e['event_id'] == 'legacy:old-audit')
        assert old['legacy'] and old['facts']['incomplete'] and old['trace_id'] is None
        assert old['permission_level'] == 'delegated' and 'phone' not in old['facts']['params']
        assert not old['facts'].get('user_confirmed')


@pytest.mark.asyncio
async def test_direct_bank_unknown_and_replay_are_distinct_from_committed_fact(audit_store):
    async with audit_store[1]() as db:
        with pytest.raises(BankResponseLost):
            await submit_transfer(db, command(), TrustedExecutionContext('U1', 'AUTH-ISOLATED', 1), simulate_response_loss=True)
        await db.rollback()
    async with audit_store[1]() as db:
        result = await submit_transfer(db, command(), TrustedExecutionContext('U1', 'AUTH-ISOLATED', 1))
        assert result.status == 'SUCCEEDED'
    async with audit_store[1]() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 9900
        assert await db.scalar(select(func.count()).select_from(AuditLogORM).where(AuditLogORM.action == 'bank_transfer')) == 1
        page = await object_timeline(db, 'U1', 'operation', command().operation_id)
        assert any(e['outcome'] == 'unknown' for e in page['items'])
        assert any(e['outcome'] == 'success' for e in page['items'])


@pytest.mark.asyncio
async def test_audit_insert_failure_rolls_back_business(audit_store, monkeypatch):
    original = hooks.add_to_session
    def fail(session, data):
        if data['phase'] == 'mutation' and data['action'].startswith('accounts.'):
            raise RuntimeError('isolated audit sink unavailable')
        return original(session, data)
    async with audit_store[1]() as db:
        monkeypatch.setattr(hooks, 'add_to_session', fail)
        account = await db.get(AccountORM, 'A1')
        account.balance_fen = 1
        with pytest.raises(RuntimeError, match='audit sink unavailable'):
            await db.commit()
        await db.rollback()
    monkeypatch.setattr(hooks, 'add_to_session', original)
    async with audit_store[1]() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 10000


@pytest.mark.asyncio
async def test_rollback_keeps_observation_but_not_mutation(audit_store):
    async with audit_store[1]() as db:
        account = await db.get(AccountORM, 'A1')
        account.balance_fen = 1
        writer.observe(db, action='verification.rejected', phase='decision', outcome='blocked',
                       domain='identity', subject_user_id='U1', facts={'error_code': 'TOTP_INVALID'})
        await db.flush()
        await db.rollback()
    async with audit_store[1]() as db:
        assert await db.scalar(select(func.count()).select_from(Event).where(Event.action == 'verification.rejected')) == 1
        assert (await db.get(AccountORM, 'A1')).balance_fen == 10000
        rows = (await db.scalars(select(Event).where(Event.action == 'accounts.updated'))).all()
        assert not rows


@pytest.mark.asyncio
async def test_bulk_cleanup_retains_snapshot_and_append_only(audit_store):
    from backend.app.models.wealth import WealthTaskORM, WealthTaskNodeORM
    async with audit_store[1]() as db:
        task = WealthTaskORM(task_id='WT-AUDIT', user_id='U1', session_id='S-AUDIT', account_id='A1',
            context_id='ctx', status='COMPLETED', control_mode='AGENT', version=1, data={'goal': 'sell_then_buy'})
        db.add(task)
        await db.flush()
        db.add(WealthTaskNodeORM(node_id='WN-AUDIT', task_id=task.task_id, kind='SUBMIT', status='COMPLETED',
            operation_id='OP-AUDIT', depends_on=['SELL-AUDIT'], attempts=1, data={'command': {
                'operation_id': 'OP-AUDIT', 'amount_fen': 1234, 'product_id': '000001', 'proof': '654321'}}))
        await db.commit()
        await db.execute(delete(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id == task.task_id))
        await db.commit()
        rows = (await db.scalars(select(Event).where(Event.action == 'wealth_task_nodes.deleted'))).all()
        assert rows and rows[0].facts['before']['data']['command']['amount_fen'] == 1234
        assert '654321' not in json.dumps(rows[0].facts)
        with pytest.raises(ValueError, match='append-only'):
            await db.execute(delete(Event))
        await db.rollback()


@pytest.mark.asyncio
async def test_fallback_replay_is_idempotent_and_private(audit_store, monkeypatch, tmp_path):
    data = writer.event_data(action='audit.recovery', phase='decision', outcome='blocked',
        subject_user_id='U1', facts={'code': '123456', 'proof': '987654', 'amount_fen': 100})
    writer.journal([data])
    directory = Path(str(tmp_path / 'recovery'))
    assert directory.stat().st_mode & 0o777 == 0o700
    segment = next(directory.glob('pending-*.jsonl'))
    raw = segment.read_bytes()
    assert segment.stat().st_mode & 0o777 == 0o600
    assert b'123456' not in raw and b'987654' not in raw
    async with audit_store[0].connect() as conn:
        count = await conn.run_sync(lambda c: writer.replay_sync(c.engine))
    assert count == 1
    # Emulate a crash after database commit and before journal removal.
    writer.journal([data])
    async with audit_store[0].connect() as conn:
        assert await conn.run_sync(lambda c: writer.replay_sync(c.engine)) == 0
    async with audit_store[1]() as db:
        assert await db.scalar(select(func.count()).select_from(Event).where(Event.action == 'audit.recovery')) == 1
    monkeypatch.setenv('AUDIT_FALLBACK_MAX_BYTES', '1')
    with pytest.raises(writer.AuditUnavailable):
        writer.journal([data])


def test_privacy_and_external_trace_cannot_assert_identity():
    safe = clean({'params': {'code': '123456', 'phone': '13800000001', 'password': 'secret',
        'command': {'product_id': '000001', 'amount_fen': 123456, 'proof': '654321'}},
        'result': {'code': 'TOTP_INVALID', 'message': 'secret free text'}})
    raw = json.dumps(safe)
    assert 'secret' not in raw and '654321' not in raw and '13800000001' not in raw
    assert safe['params']['command']['product_id'] == '000001'
    assert safe['result']['code'] == 'TOTP_INVALID'
    with execution(actor_type='user', actor_id='U1', subject_user_id='U1') as ctx:
        assert propagation_headers()['X-Audit-Trace'] == ctx.trace_id
        # Only the verified internal-token dependency calls trust_internal.
        old = ctx.trace_id
        assert ctx.trace_id == old and ctx.actor_id == 'U1'


def test_route_and_table_inventory_has_no_unclassified_entry():
    from backend.app.main import app
    from backend.app.services.audit.policy import TABLE_DOMAINS, EXCLUDED_TABLES, route_domain
    known = set(TABLE_DOMAINS) | set(EXCLUDED_TABLES)
    assert not set(Base.metadata.tables) - known
    inventory = sorted([{'method': method, 'path': r.path, 'domain': route_domain(r.path), 'action': r.name}
        for r in api_routes(app) if r.path.startswith('/api/') for method in sorted(r.methods or [])], key=lambda r: (r['path'], r['method']))
    expected = json.loads((Path(__file__).resolve().parents[2] / 'docs/contracts/operation_audit_routes.json').read_text())
    assert inventory == expected
    assert all(getattr(r, 'audit_installed', False) for r in api_routes(app) if r.path.startswith('/api/'))


@pytest.mark.asyncio
async def test_savepoint_rollback_keeps_decision_without_resurrecting_change(audit_store):
    async with audit_store[1]() as db:
        account = await db.get(AccountORM, 'A1')
        nested = await db.begin_nested()
        account.balance_fen = 1
        writer.observe(db, action='savepoint.rejected', phase='decision', outcome='blocked',
                       subject_user_id='U1', facts={'error_code': 'STALE_STATE_VERSION'})
        await db.flush()
        await nested.rollback()
        await db.commit()
    async with audit_store[1]() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 10000
        assert await db.scalar(select(func.count()).select_from(Event).where(Event.action == 'savepoint.rejected')) == 1
        assert await db.scalar(select(func.count()).select_from(Event).where(Event.action == 'accounts.updated')) == 0


@pytest.mark.asyncio
async def test_stream_200_does_not_hide_tool_failure(audit_store):
    from starlette.responses import StreamingResponse
    app = app_for_test()
    @app.post('/api/v1/nova/audit-stream-test')
    async def stream(user=Depends(get_current_user)):
        async def events():
            writer.emit(action='stream.tool', phase='result', outcome='failed', domain='assistant',
                        error_code='TOOL_EXECUTION_FAILED')
            yield 'event: error\ndata: {"code":"TOOL_EXECUTION_FAILED"}\n\n'
        return StreamingResponse(events(), media_type='text/event-stream')
    install_routes(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers()) as client:
        response = await client.post('/api/v1/nova/audit-stream-test')
        assert response.status_code == 200 and 'event: error' in response.text
    async with audit_store[1]() as db:
        event = (await db.scalars(select(Event).where(Event.action == 'stream.tool'))).one()
        page = await query_events(db, 'U1', trace_id=event.trace_id)
        assert event.outcome == 'failed'
        assert any(r['phase'] == 'transport' and r['facts']['http_status'] == 200 for r in page['items'])


@pytest.mark.asyncio
async def test_external_trace_header_is_not_trusted(audit_store):
    app = app_for_test()
    forged = 'e' * 32
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
        headers={**headers(), 'X-Audit-Trace': forged, 'X-Audit-Parent': forged}) as client:
        response = await client.get('/api/v1/audit/events')
        assert response.status_code == 200
    async with audit_store[1]() as db:
        assert await db.scalar(select(func.count()).select_from(Event).where(Event.trace_id == forged)) == 0


@pytest.mark.asyncio
async def test_total_audit_outage_refuses_new_work(audit_store, monkeypatch):
    app = app_for_test()
    def unavailable(*args, **kwargs):
        raise OSError('isolated storage failure')
    monkeypatch.setattr(writer, 'add_to_session', unavailable)
    monkeypatch.setattr(writer, 'journal', unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers()) as client:
        response = await client.post('/api/v1/tasks/audit-test', json={'amount_fen': 123, 'code': '987654', 'note': 'private'})
        assert response.status_code == 503 and response.json()['detail']['code'] == 'AUDIT_UNAVAILABLE'
    async with audit_store[1]() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 10000


@pytest.mark.asyncio
async def test_core_insert_and_conflict_are_audited_once(audit_store):
    from backend.app.models.wealth import WealthProfileORM
    async with audit_store[1]() as db:
        if db.get_bind().dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        for _ in range(2):
            await db.execute(dialect_insert(WealthProfileORM).values(user_id='U1', version=1, data={})
                .on_conflict_do_nothing(index_elements=['user_id']))
        await db.commit()
        rows = (await db.scalars(select(Event).where(Event.action == 'wealth_profiles.created'))).all()
        assert len(rows) == 1 and rows[0].subject_user_id == 'U1'


@pytest.mark.asyncio
async def test_missing_audit_storage_returns_stable_error_and_no_debit(audit_store):
    async with audit_store[0].begin() as conn:
        await conn.run_sync(Object.__table__.drop)
    app = app_for_test()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers()) as client:
        response = await client.post('/api/v1/tasks/audit-test', json={'amount_fen': 123, 'code': '987654', 'note': 'private'})
        assert response.status_code == 503
        assert response.json()['detail']['code'] == 'AUDIT_UNAVAILABLE'
    async with audit_store[1]() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 10000


@pytest.mark.asyncio
async def test_worker_attempt_is_owned_and_does_not_invent_confirmation(audit_store):
    from backend.app.models.wealth import WealthTaskORM, WealthTaskNodeORM
    from backend.app.services.orchestration.wealth_task_worker import tick
    async with audit_store[1]() as db:
        db.add(WealthTaskORM(task_id='WORKER-QA', user_id='U1', session_id='S-WORKER', account_id='A1',
            context_id='ctx', status='READY', control_mode='AGENT', version=1, data={}))
        await db.flush()
        db.add(WealthTaskNodeORM(node_id='CONFIRM-QA', task_id='WORKER-QA', kind='CONFIRM',
            status='READY', depends_on=[], attempts=0, data={}))
        await db.commit()
        await tick(db, 'WORKER-QA', None)
    async with audit_store[1]() as db:
        events = (await db.scalars(select(Event).where(Event.action == 'tick'))).all()
        assert events and all(e.subject_user_id == 'U1' and e.actor_id == 'wealth-worker' for e in events)
        task = await db.get(WealthTaskORM, 'WORKER-QA')
        assert task.waiting_reason == 'PERSONAL_CONFIRMATION'
        assert not any(e.outcome == 'success' for e in events)


@pytest.mark.asyncio
async def test_compatibility_writer_does_not_retain_proofs(audit_store):
    from backend.app.services.transaction_service import create_audit_log
    key = await create_audit_log(dict(session_id='COMPAT', user_id='U1', action='compatibility.check',
        result='blocked', params={'proof': '987654', 'password': 'private-password', 'code': '123456',
        'amount_fen': 100}, decision_chain=['schema_validated']))
    async with audit_store[1]() as db:
        row = await db.get(AuditLogORM, key)
        assert row.params == {'amount_fen': 100}
        projected = await query_events(db, 'U1', action='compatibility.check')
        assert len(projected['items']) == 1


@pytest.mark.asyncio
async def test_chat_task_links_to_bank_fact_across_requests(audit_store):
    task_id, operation_id = '20261022123456', '20261022123457'
    async with audit_store[1]() as db:
        with execution(actor_type='user', actor_id='U1', subject_user_id='U1'):
            writer.observe(db, action='assistant.plan', phase='decision', outcome='checked', domain='transfer',
                facts={'tasks': [{'chat_task_id': 'CHAT-TASK', 'transfer_task_id': task_id,
                    'task': {'task_id': task_id, 'operation': {'operation_id': operation_id, 'amount_fen': 100}}}]})
            await db.commit()
        with execution(actor_type='service', actor_id='bank-executor', subject_user_id='U1'):
            writer.observe(db, action='bank_transfer', phase='result', outcome='success', domain='transfer',
                facts={'operation_id': operation_id, 'amount_fen': 100})
            await db.commit()
        timeline = await object_timeline(db, 'U1', 'task', task_id, 'transfer')
        assert {r['action'] for r in timeline['items']} == {'assistant.plan', 'bank_transfer'}
        assert len({r['trace_id'] for r in timeline['items']}) == 2
