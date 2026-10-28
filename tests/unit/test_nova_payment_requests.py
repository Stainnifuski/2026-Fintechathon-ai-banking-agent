"""Existing payment requests are selected by bank facts and opened for user action."""
from datetime import datetime, timedelta, timezone
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Base
from backend.app.models.nova_conversation import NovaConversationORM
from backend.app.schemas.nova import PageContext
from backend.app.services.ai import nova, nova_runtime, payment_request_tools
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.ai.transfer_manager import get_session


@pytest_asyncio.fixture
async def context():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(NovaConversationORM(conversation_id='PAYMENT-CHAT', user_id='U1', title_ciphertext='test'))
        await db.commit()
        yield ToolContext('U1', db, PageContext(), original_message='查看收款申请',
                          request_id='PAYMENT-REQUEST-1', session_id='PAYMENT-CHAT')
    await engine.dispose()


@pytest.mark.asyncio
async def test_payment_request_tool_filters_and_opens_only_review_cards(context, monkeypatch):
    now = datetime.now(timezone.utc)
    requests = [
        dict(payment_request_id='OUT-1', requester_user_id='U1', payer_user_id='U2',
             requester_name='我', payer_name='李四', amount_fen=10000, amount=100,
             created_at=now, status='PENDING', state_version=1),
        dict(payment_request_id='OUT-2', requester_user_id='U1', payer_user_id='U2',
             requester_name='我', payer_name='李四', amount_fen=10000, amount=100,
             created_at=now + timedelta(minutes=1), status='PENDING', state_version=1),
        dict(payment_request_id='OUT-DONE', requester_user_id='U1', payer_user_id='U2',
             requester_name='我', payer_name='李四', amount_fen=10000, amount=100,
             created_at=now, status='PAID', state_version=2),
        dict(payment_request_id='IN-1', requester_user_id='U3', payer_user_id='U1',
             requester_name='王五', payer_name='我', amount_fen=2500, amount=25,
             created_at=now, status='PENDING', state_version=1),
    ]
    calls = []

    async def bank_list(*, user_id, role='all'):
        calls.append((user_id, role))
        return [item for item in requests if role == 'all' or
                (role == 'outgoing' and item['requester_user_id'] == user_id) or
                (role == 'incoming' and item['payer_user_id'] == user_id)]

    monkeypatch.setattr(payment_request_tools, 'list_payment_requests', bank_list)
    assert 'manage_payment_requests' in nova.TOOL_REGISTRY
    listed = await nova.execute_tool({'function': {'name': 'manage_payment_requests',
        'arguments': json.dumps({'action': 'list', 'role': 'incoming'})}}, context)
    assert [item['payment_request_id'] for item in listed['items']] == ['IN-1']

    cancelled = await payment_request_tools.manage_payment_requests(
        payment_request_tools.ManagePaymentRequestsArguments(
            action='cancel', name='李四', amount_fen=10000,
            created_from=now - timedelta(seconds=1), created_to=now + timedelta(minutes=2)), context)
    assert cancelled['matched_count'] == 2
    assert [item['payment_request_id'] for item in cancelled['items']] == ['OUT-1', 'OUT-2']
    assert [item['status'] for item in requests] == ['PENDING', 'PENDING', 'PAID', 'PENDING']

    incoming = await payment_request_tools.manage_payment_requests(
        payment_request_tools.ManagePaymentRequestsArguments(action='respond', name='王五'), context)
    assert incoming['matched_count'] == 1
    view = await get_session(context.db, 'U1', context.session_id)
    assert len(view['payment_request_tasks']) == 2
    assert [item['payment_request_id'] for item in view['payment_request_tasks'][0]['requests']] == ['OUT-1', 'OUT-2']
    assert view['payment_request_tasks'][1]['requests'][0]['payment_request_id'] == 'IN-1'
    active = await nova_runtime.inspect_active_tasks(context)
    assert {task['type'] for task in active['unfinished_execute_tasks']} == {'payment_request'}
    assert calls[:3] == [('U1', 'incoming'), ('U1', 'outgoing'), ('U1', 'incoming')]


def test_payment_request_time_filters_require_timezone():
    with pytest.raises(ValueError):
        payment_request_tools.ManagePaymentRequestsArguments(
            action='cancel', created_from=datetime(2026, 10, 26, 9))


@pytest.mark.asyncio
async def test_agent_can_open_incoming_request_card_without_paying(context, monkeypatch):
    request = dict(payment_request_id='IN-2', requester_user_id='U2', payer_user_id='U1',
                   requester_name='李四', payer_name='我', amount_fen=5000, amount=50,
                   created_at=datetime.now(timezone.utc), status='PENDING', state_version=1)

    async def bank_list(*, user_id, role='all'):
        assert user_id == 'U1'
        return [request] if role in {'all', 'incoming'} else []

    monkeypatch.setattr(payment_request_tools, 'list_payment_requests', bank_list)

    class Model:
        async def generate_structured(self, **kwargs):
            schema = kwargs['schema']
            if schema is nova_runtime.DependencyDecision:
                value = schema(depends_on_prior_task=False, reason='查看收到的申请')
            elif schema is nova_runtime.ToolSelection:
                value = schema(tool_names=['manage_payment_requests'])
            else:
                value = schema(calls=[nova_runtime.PlannedCall(
                    name='manage_payment_requests', arguments={'action': 'respond', 'name': '李四'})])
            return SimpleNamespace(data=value)

        async def generate_text(self, **_kwargs):
            return SimpleNamespace(data='已打开待处理申请，请在卡片中选择支付或拒绝。')

    result = await nova_runtime.chat('查看李四向我发出的收款申请', [], model=Model(),
                                     context=replace(context, original_message='查看李四向我发出的收款申请'))
    assert any(call['name'] == 'manage_payment_requests' and call['result'].get('matched_count') == 1
               for call in result['tool_calls'])
    assert result['tool_calls'][-1]['result']['outcome'] == 'waiting_user'
    view = await get_session(context.db, 'U1', context.session_id)
    assert view['payment_request_tasks'][0]['requests'][0]['payment_request_id'] == 'IN-2'
    assert request['status'] == 'PENDING'
