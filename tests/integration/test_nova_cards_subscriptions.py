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
    assert response.json()['tool_calls'][0]['result']['data']['status'] == 'LOCKED'
    body['page_context']['cards']['selected_card_id'] = 'C3'
    response = await client.post('/api/v1/nova/chat', headers=headers(), json=body)
    assert response.json()['tool_calls'][0]['result']['error'] == 'CARD_NOT_FOUND'
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
    assert (await nova.execute_tool(call('preview_card_operation', {'action': 'card_lock'}), home))['error'] == 'MISSING_SELECTION'
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
