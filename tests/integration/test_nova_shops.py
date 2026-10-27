"""模拟商店只读查询：价格、订单快照、付款投影、权限与页面上下文。"""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.unit.test_cake_shop import cake_store
from backend.app.models import UserORM, AccountORM, PaymentRequestORM
from backend.app.models.flower_order import FlowerOrderORM
from backend.app.models.cake_order import CakeOrderORM
from backend.app.services.flower_shop import order_service as flowers, catalog as flower_catalog
from backend.app.services.cake_shop import order_service as cakes, catalog as cake_catalog
from backend.app.services.ai import nova
from backend.app.services.ai import nova_runtime
from backend.app.services.ai.llm import LLMService
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext, ShopContext, ShopSelection
from backend.app.database.connection import get_db
from backend.app.middleware.auth import create_access_token
from backend.app.routers.nova import router

SHOPS = {'flower': (flowers, FlowerOrderORM, 'ROSE_CLASSIC'), 'cake': (cakes, CakeOrderORM, 'STRAWBERRY_CREAM')}


@pytest_asyncio.fixture
async def store(cake_store, monkeypatch):
    monkeypatch.setattr(flowers, 'AsyncSessionLocal', cake_store)
    async with cake_store() as db:
        db.add(UserORM(user_id='U007', name='花语鲜花店', phone='13800000008', id_card='demo', password_hash='hash', status='active'))
        await db.flush()
        db.add_all([AccountORM(account_id='A008', user_id='U007', account_type='savings', card_number='7', balance_fen=10000),
                    AccountORM(account_id='A2', user_id='U2', account_type='savings', card_number='2', balance_fen=100000)])
        await db.commit()
    yield cake_store


def context(db, shop='cake', selected=None, user='U1'):
    return ToolContext(user, db, PageContext(page='thirdParty', shop=ShopContext(shop=shop, selected=selected)))


def call(name, args=None):
    return {'function': {'name': name, 'arguments': json.dumps(args or {})}}


@pytest.mark.asyncio
@pytest.mark.parametrize('shop,catalog', [('flower', flower_catalog), ('cake', cake_catalog)])
async def test_catalog_and_selected_quantity_quote_are_read_only(store, shop, catalog):
    async with store() as db:
        ctx = context(db, shop, ShopSelection(type='product', id=SHOPS[shop][2], quantity=2))
        count = await db.scalar(select(func.count()).select_from(SHOPS[shop][1]))
        result = await nova.execute_tool(call('list_shop_products', {'max_price_fen': 10000}), ctx)
        assert result['data']['total'] == 1 and result['data']['items'][0]['price_fen'] == 9900
        assert result['data']['items'][0]['description'] == catalog.list_products()[0]['description']
        detail = await nova.execute_tool(call('get_shop_product'), ctx)
        assert detail['data']['estimated_total_fen'] == 19800 and detail['data']['quote_only']
        assert detail['data']['requested_quantity'] == 2
        assert await db.scalar(select(func.count()).select_from(SHOPS[shop][1])) == count
        assert (await db.get(AccountORM, 'A1')).balance_fen == 100000
        assert detail['data_kind'] == 'simulated_merchant'
        other = 'flower' if shop == 'cake' else 'cake'
        result = await nova.execute_tool(call('get_shop_product', {'shop': other}), ctx)
        assert result['error'] == 'MISSING_SELECTION'
        for args in ({'quantity': 0}, {'quantity': 21}, {'quantity': '2'}, {'user_id': 'U2'}):
            assert (await nova.execute_tool(call('get_shop_product', args), ctx))['error'] == 'INVALID_ARGUMENTS'
        assert (await nova.execute_tool(call('list_shop_products', {'min_price_fen': 20000, 'max_price_fen': 10000}), ctx))['error'] == 'INVALID_ARGUMENTS'


@pytest.mark.asyncio
@pytest.mark.parametrize('shop', ['flower', 'cake'])
@pytest.mark.parametrize('payment_status,expected', [('PENDING', 'PENDING_PAYMENT'), ('ACCEPTED', 'PENDING_PAYMENT'),
    ('PAID', 'COMPLETED'), ('REJECTED', 'PAYMENT_CLOSED'), ('CANCELLED', 'PAYMENT_CLOSED'), ('EXPIRED', 'PAYMENT_CLOSED'),
    ('expired-pending', 'PAYMENT_CLOSED'), ('refund', 'REFUNDED')])
async def test_order_payment_projection_never_mutates_or_debits(store, shop, payment_status, expected):
    service, model, product = SHOPS[shop]
    created = await service.create_order(customer_user_id='U1', product_id=product, quantity=2, idempotency_key='nova-projection')
    async with store() as db:
        payment = await db.get(PaymentRequestORM, created['payment_request_id'])
        payment.status = 'PENDING' if payment_status == 'expired-pending' else 'PAID' if payment_status == 'refund' else payment_status
        if payment_status == 'expired-pending':
            payment.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        order = await db.get(model, created['order_id'])
        if payment_status == 'refund':
            order.status = 'REFUNDED'
        await db.commit()
        await db.refresh(order)
        order_state = (order.status, order.state_version, order.updated_at)
        bank_state = (payment.status, payment.state_version)
        balance = (await db.get(AccountORM, 'A1')).balance_fen
        result = await nova.execute_tool(call('get_shop_order'), context(db, shop, ShopSelection(type='order', id=order.order_id)))
        assert result['data']['status'] == expected and result['data']['total_fen'] == 19800
        listing = await nova.execute_tool(call('list_shop_orders', {'status': expected}), context(db, shop))
        assert listing['data']['total'] == 1
        await db.refresh(order)
        await db.refresh(payment)
        assert (order.status, order.state_version, order.updated_at) == order_state
        assert (payment.status, payment.state_version) == bank_state
        assert (await db.get(AccountORM, 'A1')).balance_fen == balance
        assert 'customer_user_id' not in json.dumps(result) and 'payer_name' not in json.dumps(result)
        if payment_status == 'expired-pending':
            assert result['data']['payment_request']['status'] == 'EXPIRED'
        if payment_status == 'refund':
            assert result['data']['payment_request']['status'] == 'PAID'


@pytest.mark.asyncio
async def test_order_price_snapshot_pagination_and_user_shop_isolation(store, monkeypatch):
    owned = await cakes.create_order(customer_user_id='U1', product_id='STRAWBERRY_CREAM', quantity=2, idempotency_key='nova-own-order')
    foreign = await cakes.create_order(customer_user_id='U2', product_id='STRAWBERRY_CREAM', quantity=1, idempotency_key='nova-foreign-order')
    await cakes.create_order(customer_user_id='U1', product_id='CHOCOLATE_DELIGHT', quantity=1, idempotency_key='nova-own-order-2')
    monkeypatch.setattr(cake_catalog, '_PRODUCTS', tuple(replace(p, price_fen=12300) if p.product_id == 'STRAWBERRY_CREAM' else p for p in cake_catalog._PRODUCTS))
    async with store() as db:
        ctx = context(db)
        result = await nova.execute_tool(call('list_shop_orders', {'page_size': 1}), ctx)
        assert result['data']['total'] == 2 and result['data']['has_more'] and len(result['data']['items']) == 1
        assert (await nova.execute_tool(call('get_shop_order', {'order_id': owned['order_id']}), ctx))['data']['total_fen'] == 19800
        assert (await nova.execute_tool(call('get_shop_product', {'product_id': 'STRAWBERRY_CREAM'}), ctx))['data']['product']['price_fen'] == 12300
        for key in ('missing', foreign['order_id']):
            assert (await nova.execute_tool(call('get_shop_order', {'order_id': key}), ctx))['error'] == 'ORDER_NOT_FOUND'
        assert (await nova.execute_tool(call('get_shop_order', {'shop': 'flower', 'order_id': owned['order_id']}), ctx))['error'] == 'ORDER_NOT_FOUND'
        assert (await nova.execute_tool(call('get_shop_order'), ctx))['error'] == 'MISSING_SELECTION'
        assert (await nova.execute_tool(call('list_shop_products'), ToolContext('U1', db, PageContext())))['error'] == 'MISSING_SHOP'
        assert (await nova.execute_tool(call('list_shop_orders', {'user_id': 'U2'}), ctx))['error'] == 'INVALID_ARGUMENTS'
        # 错误绑定他人的收款申请也不能泄露付款状态。
        row = await db.get(CakeOrderORM, owned['order_id'])
        unlinked = PaymentRequestORM(payment_request_id='PR-OTHER-PAYER', requester_user_id='U008',
            payer_user_id='U2', receive_account_id='A009', amount_fen=9900, currency='CNY',
            status='PAID', state_version=1, idempotency_key='nova-unlinked-payment',
            expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        db.add(unlinked)
        await db.flush()
        row.payment_request_id = unlinked.payment_request_id
        await db.commit()
        result = await nova.execute_tool(call('get_shop_order', {'order_id': owned['order_id']}), ctx)
        assert not result['data']['payment_status_available'] and result['data']['payment_request'] is None


@pytest.mark.asyncio
async def test_offline_context_and_native_authenticated_tool_loop(store, monkeypatch):
    created = await cakes.create_order(customer_user_id='U1', product_id='STRAWBERRY_CREAM', quantity=1, idempotency_key='nova-api-order')
    async with store() as db:
        ctx = context(db, selected=ShopSelection(type='order', id=created['order_id']))
        reply = await nova.chat('这个订单付款了吗？', [], context=ctx)
        assert reply['mode'] == 'offline' and reply['tool_calls'][0]['name'] == 'get_shop_order'
        assert '等待付款' in reply['reply']
        balance = await nova.chat('查询账户余额', [], context=ctx)
        assert balance['tool_calls'][0]['name'] == 'get_financial_overview'
        calendar = await nova.chat('今天有什么日程安排？', [], context=ctx)
        assert calendar['tool_calls'][0]['name'] == 'list_calendar_events'
        assert not (await nova.chat('帮我购买这款蛋糕', [], context=ctx))['tool_calls']
    seen = []
    class ReadModel:
        async def generate_structured(self, **kwargs):
            from types import SimpleNamespace
            schema = kwargs['schema']
            seen.append(schema)
            if schema is nova_runtime.DependencyDecision:
                assert created['order_id'] in kwargs['messages'][1]['content']
                return SimpleNamespace(data=schema(depends_on_prior_task=False, reason='selected order'))
            if schema is nova_runtime.ToolSelection:
                return SimpleNamespace(data=schema(tool_names=['get_shop_order']))
            return SimpleNamespace(data=schema(calls=[nova_runtime.PlannedCall(
                name='get_shop_order', arguments={})]))
        async def generate_text(self, **kwargs):
            from types import SimpleNamespace
            reply = '该订单正在等待付款。'
            if kwargs.get('on_delta'):
                await kwargs['on_delta'](reply)
            return SimpleNamespace(data=reply)
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    model = ReadModel()
    monkeypatch.setattr(nova_runtime, 'llm', model)
    app = FastAPI()
    app.include_router(router)
    async def get_session():
        async with store() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U1'})
            shop = {'shop': 'cake', 'selected': {'type': 'order', 'id': created['order_id']}}
            result = await client.post('/api/v1/nova/chat', json={'message': '这个订单付款了吗？', 'page_context': {'page': 'thirdParty', 'shop': shop}})
            assert result.status_code == 200 and '等待付款' in result.json()['reply']
            assert seen == [nova_runtime.DependencyDecision, nova_runtime.ToolSelection,
                            nova_runtime.ToolPlan]
            assert 'grounding' not in result.json()
            for bad in ({'shop': 'other'}, {'shop': 'cake', 'user_id': 'U2'}, {'shop': 'cake', 'selected': {'type': 'order', 'id': 'x', 'quantity': 2}}):
                assert (await client.post('/api/v1/nova/chat', json={'message': '查询', 'page_context': {'page': 'thirdParty', 'shop': bad}})).status_code == 422
            assert (await client.post('/api/v1/nova/chat', json={'message': '查询', 'page_context': {'page': 'home', 'shop': shop}})).status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_purchase_orders_when_complete_otherwise_prefilled_form_and_never_pays(store):
    from backend.app.middleware.auth import get_current_user
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.bank import nova_conversations
    async with store() as db:
        session_id = (await nova_conversations.create(db, 'U1'))['session_id']
        balance = (await db.get(AccountORM, 'A1')).balance_fen

        def at(message, request_id, shop='cake', selected=None):
            return replace(context(db, shop, selected), original_message=message,
                           session_id=session_id, request_id=request_id, task_switch_approved=True)

        complete = await nova.execute_tool(call('prepare_shop_purchase', {
            'shop': 'cake', 'product_id': 'STRAWBERRY_CREAM', 'quantity': 2, 'quantity_quote': '两个'}),
            at('买两个草莓奶油蛋糕', 'R1'))
        order = complete['data']['order']
        assert complete['data']['created'] and order['quantity'] == 2 and order['total_fen'] == 19800
        assert order['status'] == 'PENDING_PAYMENT'
        assert await db.scalar(select(func.count()).select_from(PaymentRequestORM)) == 1
        assert (await db.get(AccountORM, 'A1', populate_existing=True)).balance_fen == balance

        no_quantity = await nova.execute_tool(call('prepare_shop_purchase', {
            'shop': 'cake', 'product_id': 'STRAWBERRY_CREAM'}), at('买个草莓蛋糕吧', 'R2'))
        assert not no_quantity['data']['created'] and no_quantity['data']['missing_fields'] == ['数量']
        invented = await nova.execute_tool(call('prepare_shop_purchase', {
            'shop': 'flower', 'product_id': 'ROSE_CLASSIC', 'quantity': 5, 'quantity_quote': '五束'}),
            at('想买点玫瑰', 'R3', 'flower'))
        assert not invented['data']['created'] and invented['data']['prefill'] == {'product_id': 'ROSE_CLASSIC'}
        unknown = await nova.execute_tool(call('prepare_shop_purchase', {
            'shop': 'cake', 'product_id': 'NOT_A_CAKE', 'quantity': 1, 'quantity_quote': '一个'}), at('买一个蛋糕', 'R4'))
        assert unknown['data']['missing_fields'] == ['商品']
        selected = await nova.execute_tool(call('prepare_shop_purchase', {'shop': 'flower'}),
            at('就买这个', 'R5', 'flower', ShopSelection(type='product', id='ROSE_CLASSIC', quantity=1)))
        assert selected['data']['prefill'] == {'product_id': 'ROSE_CLASSIC'}
        assert await db.scalar(select(func.count()).select_from(PaymentRequestORM)) == 1

        tasks = (await get_session(db, 'U1', session_id))['shop_tasks']
        assert [bool(task['order']) for task in tasks] == [True, False, False, False, False]
        assert tasks[0]['order']['order_id'] == order['order_id'] and tasks[0]['shop_name']

        app = FastAPI()
        app.include_router(router)
        user = {'user_id': 'U1'}
        app.dependency_overrides[get_current_user] = lambda: user
        async def use_db():
            yield db
        app.dependency_overrides[get_db] = use_db
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            base = f"/api/v1/nova/task-sessions/{session_id}/shop-tasks"
            # The user completes the form; the order is placed through the shop API, then linked.
            placed = await cakes.create_order(customer_user_id='U1', product_id='STRAWBERRY_CREAM', quantity=1,
                                              idempotency_key='chat-form-order-1')
            foreign = await cakes.create_order(customer_user_id='U2', product_id='STRAWBERRY_CREAM', quantity=1,
                                               idempotency_key='chat-form-order-2')
            url = f"{base}/{tasks[1]['entry_id']}/order"
            assert (await client.post(url, json={'shop': 'cake', 'order_id': foreign['order_id']})).status_code == 404
            linked = (await client.post(url, json={'shop': 'cake', 'order_id': placed['order_id']})).json()['shop_tasks']
            assert linked[1]['order']['order_id'] == placed['order_id']
            assert (await client.post(f"{base}/{tasks[0]['entry_id']}/dismiss")).status_code == 409
            assert (await client.post(f"{base}/{tasks[2]['entry_id']}/dismiss")).status_code == 200
    assert '店铺业务 Skill' in nova.SYSTEM_PROMPT
    assert not [name for name in nova.TOOL_REGISTRY if any(word in name for word in ('pay', 'accept', 'confirm'))]


@pytest.mark.asyncio
async def test_flower_purchase_tool_creates_pending_order_card_without_payment(store):
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.bank import nova_conversations

    async with store() as db:
        session_id = (await nova_conversations.create(db, 'U1'))['session_id']
        before = (await db.get(AccountORM, 'A1')).balance_fen
        tool_context = replace(context(db, 'flower'), original_message='买两束经典玫瑰',
                               session_id=session_id, request_id='flower-order-1')
        result = await nova.execute_tool(call('prepare_shop_purchase', {
            'shop': 'flower', 'product_id': 'ROSE_CLASSIC', 'quantity': 2, 'quantity_quote': '两束'}), tool_context)
        assert result['data']['created']
        assert result['data']['order']['status'] == 'PENDING_PAYMENT'
        assert result['data']['order']['total_fen'] == 19800
        tasks = (await get_session(db, 'U1', session_id))['shop_tasks']
        assert len(tasks) == 1 and tasks[0]['order']['product_id'] == 'ROSE_CLASSIC'
        records = await nova_runtime._shop_records(tool_context)
        assert records[0]['status'] == 'PENDING_PAYMENT' and not records[0]['completed']
        assert await db.scalar(select(func.count()).select_from(PaymentRequestORM)) == 1
        assert (await db.get(AccountORM, 'A1', populate_existing=True)).balance_fen == before
