"""NOVA 卡片/订阅：页面事实一致、身份隔离、查询只读、卡片操作仅生成预览和上下文闭环。"""
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from tests.integration.test_cards import db, client, headers
from backend.app.models import AccountORM, CardORM, CardOperationORM, SubscriptionORM, TransactionORM
from backend.app.schemas.nova import PageContext, CardContext, SubscriptionContext
from backend.app.services.ai import nova
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.bank import card_service, subscription_service


def call(name, arguments=None):
    return {'function': {'name': name, 'arguments': json.dumps(arguments or {})}}


def context(db, page='home', **fields):
    return ToolContext('U1', db, PageContext(page=page, **fields))


async def seed_subscriptions(db):
    for key, user, account, status, amount in [('S1', 'U1', 'A1', 'ACTIVE', 2500), ('S2', 'U1', 'A1', 'CANCELLED', 9900), ('S3', 'U2', 'A3', 'ACTIVE', 8000), ('S4', 'U1', 'A1', 'ACTIVE', 1500)]:
        db.add(SubscriptionORM(subscription_id=key, user_id=user, debit_account_id=account,
            merchant_name='音乐会员' if key != 'S4' else '云盘会员', amount_fen=amount,
            currency='CNY', billing_cycle='MONTHLY', next_charge_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
            status=status, state_version=1, cancelled_at=datetime(2026, 10, 1, tzinfo=timezone.utc) if status == 'CANCELLED' else None))
    await db.commit()


@pytest.mark.asyncio
async def test_cards_equal_page_masked_and_filters(db):
    card = await db.get(CardORM, 'C1')
    card.card_number = '6222000012340001'
    (await db.get(AccountORM, 'A1')).card_number = '6222000099990001'
    await db.commit()
    result = await nova.execute_tool(call('list_cards', {'page_size': 1}), context(db))
    assert result['data']['total'] == 2 and result['data']['has_more']
    assert result['data_kind'] == 'simulated_bank' and result['amount_unit'] == 'fen'
    detail = await nova.execute_tool(call('get_card'), context(db, 'cards', cards=CardContext(selected_card_id='C1')))
    page = (await card_service.list_cards(db, user_id='U1'))[0]
    for key in ('status', 'account_id', 'single_txn_limit_fen', 'daily_txn_limit_fen', 'state_version'):
        assert detail['data'][key] == page[key]
    assert detail['data']['card_number'] == '****0001'
    assert '62220000' not in json.dumps(detail) and 'user_id' not in json.dumps(detail)
    result = await nova.execute_tool(call('list_cards', {'status': 'LOCKED', 'card_type': 'CREDIT', 'last_four': '0002'}), context(db))
    assert result['data']['total'] == 1 and result['data']['items'][0]['credit_limit_fen'] == 2000000
    assert (await nova.execute_tool(call('list_cards', {'is_virtual': True}), context(db)))['data']['total'] == 0


@pytest.mark.asyncio
async def test_subscriptions_equal_page_summary_all_pages_and_not_charges(db):
    await seed_subscriptions(db)
    result = await nova.execute_tool(call('list_subscriptions', {'page_size': 1}), context(db))
    assert result['data']['total'] == 3 and len(result['data']['items']) == 1
    assert result['data']['summary']['monthly_commitment_fen'] == 4000
    assert result['data']['summary']['active_count'] == 2
    assert result['data']['summary']['cancelled_count'] == 1
    detail = await nova.execute_tool(call('get_subscription'), context(db, 'subscriptions', subscriptions=SubscriptionContext(selected_subscription_id='S1')))
    page = (await subscription_service.list_subscriptions(user_id='U1', session=db))[0]
    for key in ('merchant_name', 'amount_fen', 'status', 'billing_cycle', 'state_version'):
        assert detail['data'][key] == page[key]
    assert detail['data']['debit_account_label'] == '****0001' and 'amount' not in detail['data']
    cancelled = await nova.execute_tool(call('list_subscriptions', {'status': 'CANCELLED'}), context(db))
    assert cancelled['data']['summary']['monthly_commitment_fen'] == 0
    assert cancelled['data']['items'][0]['cancelled_at'].endswith('Z')
    filtered = await nova.execute_tool(call('list_subscriptions', {'keyword': '云盘'}), context(db))
    assert filtered['data']['total'] == 1 and filtered['data']['summary']['monthly_commitment_fen'] == 1500


@pytest.mark.asyncio
async def test_summary_excludes_non_monthly_and_non_cny(db):
    await seed_subscriptions(db)
    (await db.get(SubscriptionORM, 'S1')).currency = 'USD'
    (await db.get(SubscriptionORM, 'S4')).billing_cycle = 'YEARLY'
    await db.commit()
    data = (await nova.execute_tool(call('list_subscriptions'), context(db)))['data']
    assert data['summary']['monthly_commitment_fen'] == 0 and data['summary']['active_count'] == 2


@pytest.mark.asyncio
async def test_ownership_explicit_and_forged_page_selection(db):
    await seed_subscriptions(db)
    for name, key, foreign, page, field in [('get_card', 'card_id', 'C3', 'cards', {'cards': CardContext(selected_card_id='C3')}),
        ('get_subscription', 'subscription_id', 'S3', 'subscriptions', {'subscriptions': SubscriptionContext(selected_subscription_id='S3')})]:
        code = 'CARD_NOT_FOUND' if name == 'get_card' else 'SUBSCRIPTION_NOT_FOUND'
        for id in (foreign, 'missing'):
            assert (await nova.execute_tool(call(name, {key: id}), context(db)))['error'] == code
        assert (await nova.execute_tool(call(name), context(db, page, **field)))['error'] == code
        assert (await nova.execute_tool(call(name), context(db)))['error'] == 'MISSING_SELECTION'
    # 即使构造了属于本人、关联他人账户的脏数据，也不返回跨账户事实。
    (await db.get(CardORM, 'C1')).account_id = 'A3'
    (await db.get(SubscriptionORM, 'S1')).debit_account_id = 'A3'
    await db.commit()
    assert (await nova.execute_tool(call('get_card', {'card_id': 'C1'}), context(db)))['error'] == 'CARD_NOT_FOUND'
    assert (await nova.execute_tool(call('get_subscription', {'subscription_id': 'S1'}), context(db)))['error'] == 'SUBSCRIPTION_NOT_FOUND'


@pytest.mark.asyncio
async def test_strict_arguments_and_no_write_tools(db):
    for name in ('list_cards', 'get_card', 'list_subscriptions', 'get_subscription'):
        assert (await nova.execute_tool(call(name)))['error'] == 'AUTH_CONTEXT_REQUIRED'
        for arguments in ({'user_id': 'U2'}, {'state_version': 1}, {'confirmation_token': 'secret'}):
            assert (await nova.execute_tool(call(name, arguments), context(db)))['error'] == 'INVALID_ARGUMENTS'
    for name, arguments in [('list_cards', {'page_size': 51}), ('list_cards', {'is_virtual': 'true'}),
        ('list_cards', {'last_four': '1'}), ('list_subscriptions', {'keyword': ' '}),
        ('list_subscriptions', {'status': 'bad'}), ('get_subscription', {'subscription_id': 'a' * 41})]:
        assert (await nova.execute_tool(call(name, arguments), context(db)))['error'] == 'INVALID_ARGUMENTS'
    for name in ('cancel_subscription', 'simulate_subscription_charge', 'card_unlock', 'card_report_lost',
                 'confirm_card_operation', 'verify_card_operation', 'simulate_card_purchase'):
        assert (await nova.execute_tool(call(name), context(db)))['error'] == 'UNKNOWN_TOOL'


@pytest.mark.parametrize('page, fields', [('home', {'cards': {'selected_card_id': 'C1'}}),
    ('cards', {'subscriptions': {'selected_subscription_id': 'S1'}}),
    ('subscriptions', {'subscriptions': {'amount_fen': 1}})])
def test_context_rejects_wrong_page_and_client_facts(page, fields):
    with pytest.raises(ValidationError):
        PageContext(page=page, **fields)


@pytest.mark.asyncio
async def test_offline_and_queries_do_not_mutate_bank(db):
    await seed_subscriptions(db)
    ctx = context(db, 'cards', cards=CardContext(selected_card_id='C2'))
    for question, expected in [('这张卡的状态和限额是多少？', 'get_card'), ('查看我的信用卡', 'list_cards'),
        ('查看已锁定的卡片', 'list_cards'), ('查看已注销的卡片', 'list_cards'),
        ('我的订阅每月预计要花多少钱？', 'list_subscriptions'), ('查看已取消的订阅', 'list_subscriptions')]:
        result = await nova.chat(question, [], context=ctx)
        assert result['tool_calls'][0]['name'] == expected and not result['tool_calls'][0]['result'].get('error')
    ctx = context(db, 'subscriptions', subscriptions=SubscriptionContext(selected_subscription_id='S1'))
    result = await nova.chat('这个订阅什么时候扣费？', [], context=ctx)
    assert result['tool_calls'][0]['name'] == 'get_subscription'
    for question in ('请取消订阅', '请帮我解锁这张卡', '给这个订阅模拟扣费', '把这张卡挂失', '帮我把这个订阅取消',
                     '帮我把这张卡临时锁上', '把这张卡的境外交易打开', '给这张卡补卡', '帮我锁定这张卡'):
        result = await nova.chat(question, [], context=ctx)
        assert result['tool_calls'] == []
    assert (await db.get(AccountORM, 'A1')).balance_fen == 100000
    assert (await db.get(CardORM, 'C2')).status == 'LOCKED' and (await db.get(CardORM, 'C2')).state_version == 1
    assert (await db.get(SubscriptionORM, 'S1')).next_charge_at.day == 12
    assert (await db.get(SubscriptionORM, 'S1')).state_version == 1
    for model in (CardOperationORM, TransactionORM):
        assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_nova_http_context_and_auth(client):
    body = {'message': '这张卡的状态和限额是多少？', 'page_context': {'version': 1, 'page': 'cards', 'cards': {'selected_card_id': 'C2'}}}
    assert (await client.post('/api/v1/nova/chat', json=body)).status_code == 401
    response = await client.post('/api/v1/nova/chat', headers=headers(), json=body)
    assert response.status_code == 200
    assert response.json()['tool_calls'] == []
    assert response.json()['mode'] == 'offline'
    body['page_context']['cards']['selected_card_id'] = 'C3'
    response = await client.post('/api/v1/nova/chat', headers=headers(), json=body)
    assert response.json()['tool_calls'] == []
    body['page_context']['user_id'] = 'U2'
    assert (await client.post('/api/v1/nova/chat', headers=headers(), json=body)).status_code == 422


@pytest.mark.asyncio
async def test_preview_tool_only_opens_pending_operation(client, db):
    ctx = context(db, 'cards', cards=CardContext(selected_card_id='C1'))
    result = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock'}), ctx)
    data = result['data']
    assert result['source'] == 'cards.operation_preview'
    assert (data['executed'], data['permission_level'], data['card_id'], data['status']) == (
        False, 'yellow', 'C1', 'AWAITING_CONFIRMATION')
    assert data['operation_id'].startswith('NOVA-') and 'digest' not in data and '尚未执行' in data['next_step']
    assert (await db.get(CardORM, 'C1', populate_existing=True)).status == 'ACTIVE'

    # NOVA 生成的预览只能由本人在卡片接口确认。
    view = (await client.get(f"/api/v1/cards/operations/{data['operation_id']}", headers=headers())).json()
    assert (await client.get(f"/api/v1/cards/operations/{data['operation_id']}", headers=headers('U2'))).status_code == 404
    done = await client.post(f"/api/v1/cards/operations/{data['operation_id']}/confirm", headers=headers(),
                             json={'digest': view['digest']})
    assert done.json()['status'] == 'COMPLETED'
    assert (await db.get(CardORM, 'C1', populate_existing=True)).status == 'LOCKED'


@pytest.mark.asyncio
async def test_preview_tool_fills_unmentioned_fields_and_levels_by_direction(db):
    ctx = context(db, 'cards', cards=CardContext(selected_card_id='C1'))
    overseas = await nova.execute_tool(call('preview_card_operation', {'action': 'card_set_channels', 'overseas_enabled': True}), ctx)
    assert overseas['data']['permission_level'] == 'red'
    assert [change['field'] for change in overseas['data']['changes']] == ['overseas_enabled']
    lower = await nova.execute_tool(call('preview_card_operation', {'action': 'card_set_transaction_limits', 'daily_txn_limit': '30000'}),
                                   replace(ctx, original_message='将每日交易限额降到30000元'))
    assert lower['data']['permission_level'] == 'yellow'
    assert [change['after'] for change in lower['data']['changes']] == [1_000_000, 3_000_000]
    apply = await nova.execute_tool(call('preview_card_operation', {'action': 'card_apply_virtual', 'account_id': 'A1'}), ctx)
    assert apply['data']['changes'][0]['after'] == '****0001'
    assert await db.scalar(select(func.count()).select_from(CardOperationORM)) == 3
    assert await db.scalar(select(func.count()).select_from(CardORM).where(CardORM.is_virtual.is_(True))) == 0


@pytest.mark.asyncio
async def test_preview_tool_rejects_missing_foreign_and_malformed_requests(db):
    home = context(db)
    missing = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock'}), home)
    assert missing['source'] == 'cards.operation_form' and missing['data']['missing_fields'] == ['目标卡片']
    foreign = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock', 'card_id': 'C3'}), home)
    assert foreign['error'] == 'CARD_NOT_FOUND'
    extra = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock', 'card_id': 'C1', 'credit_limit': '1'}), home)
    assert extra['error'] == 'INVALID_ARGUMENTS'
    for arguments in ({'action': 'card_adjust_credit_limit', 'card_id': 'C2', 'credit_limit': '1.001'},
                      {'action': 'card_lock', 'card_id': 'C1', 'user_id': 'U2'}, {'action': 'card_delete'}):
        assert (await nova.execute_tool(call('preview_card_operation', arguments), home))['error'] == 'INVALID_ARGUMENTS'
    locked = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock', 'card_id': 'C2'}), home)
    assert locked['error'] == 'CARD_NOT_ACTIVE'
    assert await db.scalar(select(func.count()).select_from(CardOperationORM)) == 0


@pytest.mark.asyncio
async def test_card_transactions_tool_explains_declines(db):
    await card_service.simulate_card_purchase(db, user_id='U1', card_id='C2', operation_id='PURCHASE-LOCKED',
                                              amount_fen=1000, merchant='便利店', channel='pos')
    await db.commit()
    ctx = context(db, 'cards', cards=CardContext(selected_card_id='C2'))
    result = await nova.execute_tool(call('list_card_transactions'), ctx)
    data = result['data']
    assert result['source'] == 'cards.authorizations' and data['total'] == 1
    assert data['items'][0]['approved'] is False and data['items'][0]['message']
    assert data['card_number'].startswith('****') and 'user_id' not in json.dumps(result)
    declined = await nova.execute_tool(call('list_card_transactions', {'approved': True}), ctx)
    assert declined['data']['total'] == 0
    from backend.app.services.ai.grounding import evidence_for
    assert '被拒绝' in evidence_for(0, {'name': 'list_card_transactions', 'result': result}).text
    foreign = await nova.execute_tool(call('list_card_transactions', {'card_id': 'C3'}), context(db))
    assert foreign['error'] == 'CARD_NOT_FOUND'


@pytest.mark.asyncio
async def test_preview_appears_as_chat_task_card_confirmed_only_by_user(client, db):
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.bank import nova_conversations
    session_id = (await nova_conversations.create(db, 'U1'))['session_id']
    ctx = replace(context(db, 'cards', cards=CardContext(selected_card_id='C1')),
                  session_id=session_id, request_id='REQ-card-1')
    result = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock'}), ctx)
    assert '聊天中的卡片任务卡' in result['data']['next_step']
    session = await get_session(db, 'U1', session_id)
    task = session['card_operations'][0]
    card = task['operation']
    assert (card['operation_id'], task['origin_request_id']) == (result['data']['operation_id'], 'REQ-card-1')
    assert card['status'] == 'AWAITING_CONFIRMATION' and 'confirm' in card['allowed_actions']
    assert task['card']['card_id'] == 'C1' and session['session_version'] == 0
    done = await client.post(f"/api/v1/cards/operations/{card['operation_id']}/confirm", headers=headers(),
                             json={'digest': card['digest']})
    assert done.json()['status'] == 'COMPLETED'
    refreshed = (await get_session(db, 'U1', session_id))['card_operations'][0]['operation']
    assert refreshed['status'] == 'COMPLETED' and refreshed['allowed_actions'] == []


def test_agent_has_card_skill_but_no_authorization_tools():
    assert '卡片业务 Skill' in nova.SYSTEM_PROMPT and 'card_apply_virtual' in nova.SYSTEM_PROMPT
    assert 'list_card_transactions' in nova.TOOL_REGISTRY
    assert not [name for name in nova.TOOL_REGISTRY if any(word in name for word in ('confirm', 'verify', 'authorize'))]


@pytest.mark.asyncio
async def test_bank_rejection_reason_reaches_user(db):
    from backend.app.services.ai.grounding import evidence_for
    result = await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock', 'card_id': 'C2'}), context(db))
    text = evidence_for(0, {'name': 'preview_card_operation', 'result': result}).text
    assert result['error'] == 'CARD_NOT_ACTIVE' and '银行未受理' in text and '未生成任务卡' in text



@pytest.mark.asyncio
async def test_incomplete_or_rejected_values_open_form_in_chat_and_user_completes_it(client, db):
    """Agent opens the same form in chat; only the user generates, edits and confirms the preview."""
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.bank import nova_conversations
    session_id = (await nova_conversations.create(db, 'U1'))['session_id']
    (await db.get(CardORM, 'C2')).status = 'ACTIVE'
    await db.commit()
    ctx = replace(context(db), session_id=session_id, request_id='REQ-form-1')
    opened = await nova.execute_tool(call('preview_card_operation', {'action': 'card_adjust_credit_limit', 'card_id': 'C2'}), ctx)
    assert opened['data']['form_opened'] and opened['data']['missing_fields'] == ['新信用额度']
    assert await db.scalar(select(func.count()).select_from(CardOperationORM)) == 0
    task = (await get_session(db, 'U1', session_id))['card_operations'][0]
    assert task['operation'] is None and task['action'] == 'card_adjust_credit_limit' and task['card']['card_id'] == 'C2'

    # The user fills the chat form; the preview is created through the authenticated card API.
    preview = (await client.post('/api/v1/cards/operations', headers=headers(), json={
        'operation_id': 'chat-form-op-1', 'action': 'card_adjust_credit_limit', 'card_id': 'C2', 'credit_limit': '15000'})).json()
    url = f"/api/v1/nova/task-sessions/{session_id}/card-tasks/{task['entry_id']}/operation"
    assert (await client.post(url, headers=headers('U2'), json={'operation_id': 'chat-form-op-1'})).status_code == 404
    linked = await client.post(url, headers=headers(), json={'operation_id': 'chat-form-op-1'})
    assert linked.status_code == 200, linked.text
    assert linked.json()['card_operations'][0]['operation']['operation_id'] == preview['operation_id']

    # Editing replaces the unconfirmed preview; the older one is cancelled, never executed.
    await client.post('/api/v1/cards/operations', headers=headers(), json={
        'operation_id': 'chat-form-op-2', 'action': 'card_adjust_credit_limit', 'card_id': 'C2', 'credit_limit': '12000'})
    assert (await client.post(url, headers=headers(), json={'operation_id': 'chat-form-op-2'})).status_code == 200
    assert (await db.get(CardOperationORM, 'chat-form-op-1', populate_existing=True)).status == 'CANCELLED'
    mismatched = await client.post('/api/v1/cards/operations', headers=headers(), json={
        'operation_id': 'chat-form-op-3', 'action': 'card_lock', 'card_id': 'C1'})
    assert mismatched.status_code == 201
    assert (await client.post(url, headers=headers(), json={'operation_id': 'chat-form-op-3'})).status_code == 422
    assert (await db.get(CardORM, 'C2', populate_existing=True)).credit_limit_fen == 2000000

    rejected = await nova.execute_tool(call('preview_card_operation', {
        'action': 'card_set_transaction_limits', 'card_id': 'C1', 'single_txn_limit': '80000'}),
        replace(ctx, original_message='单笔限额改成80000元', request_id='REQ-form-2'))
    assert rejected['data']['form_opened'] and rejected['data']['bank_message']
    tasks = (await get_session(db, 'U1', session_id))['card_operations']
    assert tasks[-1]['notice'] == rejected['data']['bank_message']
    dismissed = await client.post(f"/api/v1/nova/task-sessions/{session_id}/card-tasks/{tasks[-1]['entry_id']}/dismiss",
                                  headers=headers())
    assert len(dismissed.json()['card_operations']) == 1


@pytest.mark.asyncio
async def test_subscription_cancellation_is_a_chat_task_the_agent_cannot_confirm(client, db):
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.ai.grounding import evidence_for
    from backend.app.services.bank import nova_conversations
    await seed_subscriptions(db)
    session_id = (await nova_conversations.create(db, 'U1'))['session_id']
    ctx = replace(context(db, 'subscriptions', subscriptions=SubscriptionContext(selected_subscription_id='S1')),
                  session_id=session_id, request_id='REQ-sub-1')
    result = await nova.execute_tool(call('request_subscription_cancellation'), ctx)
    data = result['data']
    assert data['executed'] is False and data['merchant_name'] == '音乐会员' and not data['form_opened']
    assert 'token' not in json.dumps(result) and '本人确认' in data['next_step']
    assert '尚未取消' in evidence_for(0, {'name': 'request_subscription_cancellation', 'result': result}).text
    assert (await db.get(SubscriptionORM, 'S1', populate_existing=True)).status == 'ACTIVE'
    task = (await get_session(db, 'U1', session_id))['subscription_tasks'][0]
    assert task['subscription']['subscription_id'] == 'S1' and task['origin_request_id'] == 'REQ-sub-1'

    cancelled = await nova.execute_tool(call('request_subscription_cancellation', {'subscription_id': 'S2'}), ctx)
    assert cancelled['error'] == 'SUBSCRIPTION_NOT_ACTIVE'
    foreign = await nova.execute_tool(call('request_subscription_cancellation', {'subscription_id': 'S3'}), ctx)
    assert foreign['error'] == 'SUBSCRIPTION_NOT_FOUND'

    # Unnamed: the card lists active subscriptions; the user's selection is owner-checked.
    picker = await nova.execute_tool(call('request_subscription_cancellation'),
                                     replace(context(db), session_id=session_id, request_id='REQ-sub-2'))
    assert picker['data']['form_opened']
    entry = (await get_session(db, 'U1', session_id))['subscription_tasks'][-1]
    assert entry['subscription'] is None
    base = f"/api/v1/nova/task-sessions/{session_id}/subscription-tasks/{entry['entry_id']}"
    for key, code in (('S3', 404), ('S2', 409)):
        assert (await client.post(base + '/select', headers=headers(), json={'subscription_id': key})).status_code == code
    chosen = await client.post(base + '/select', headers=headers(), json={'subscription_id': 'S4'})
    assert chosen.json()['subscription_tasks'][-1]['subscription']['merchant_name'] == '云盘会员'
    assert (await client.post(base + '/dismiss', headers=headers('U2'))).status_code == 404
    remaining = (await client.post(base + '/dismiss', headers=headers())).json()['subscription_tasks']
    assert [item['subscription_id'] for item in remaining] == ['S1']
    assert (await db.get(SubscriptionORM, 'S4', populate_existing=True)).status == 'ACTIVE'
    assert '订阅业务 Skill' in nova.SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_channel_preview_text_shows_on_off(db):
    from backend.app.services.ai.grounding import evidence_for
    ctx = context(db, 'cards', cards=CardContext(selected_card_id='C1'))
    result = await nova.execute_tool(call('preview_card_operation', {'action': 'card_set_channels', 'overseas_enabled': True}), ctx)
    text = evidence_for(0, {'name': 'preview_card_operation', 'result': result}).text
    assert '“关闭” → “开启”' in text and 'True' not in text
