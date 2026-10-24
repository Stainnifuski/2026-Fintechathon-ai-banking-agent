"""店铺商品、报价和本人订单查询，以及聊天中的下单（付款仍由本人确认）。"""
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from ...domain.time import utc_iso, utc_now
from ...schemas.nova import StrictModel
from .. import shop_reads
from ..cake_shop import CakeShopError
from ..flower_shop import FlowerShopError

Shop = Literal['flower', 'cake']
Status = Literal['PENDING_PAYMENT', 'COMPLETED', 'PAYMENT_CLOSED', 'REFUNDED','PARTIALLY_REFUNDED']


class ShopArguments(StrictModel):
    shop: Shop | None = None


class ProductListArguments(ShopArguments):
    keyword: str | None = Field(default=None, min_length=1, max_length=200)
    min_price_fen: int | None = Field(default=None, ge=0, le=100000000)
    max_price_fen: int | None = Field(default=None, ge=0, le=100000000)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)

    @field_validator('keyword')
    @classmethod
    def valid_keyword(cls, value):
        if value is not None and not value.strip():
            raise ValueError('关键词不能为空')
        return value.strip() if value else value

    @model_validator(mode='after')
    def valid_price(self):
        if self.min_price_fen is not None and self.max_price_fen is not None and self.min_price_fen > self.max_price_fen:
            raise ValueError('最低价不能高于最高价')
        return self


class ProductArguments(ShopArguments):
    product_id: str | None = Field(default=None, min_length=1, max_length=32)
    quantity: int | None = Field(default=None, ge=1, le=20)


class OrderListArguments(ShopArguments):
    status: Status | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=80)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)

    @field_validator('keyword')
    @classmethod
    def valid_keyword(cls, value):
        if value is not None and not value.strip():
            raise ValueError('关键词不能为空')
        return value.strip() if value else value


class OrderArguments(ShopArguments):
    order_id: str | None = Field(default=None, min_length=1, max_length=40)


class PurchaseArguments(ShopArguments):
    product_id: str | None = Field(default=None, min_length=1, max_length=32,
        description='商品ID，必须来自 list_shop_products 结果或页面选中商品')
    quantity: int | None = Field(default=None, ge=1, le=20, description='购买数量1-20；用户没说数量时不填')
    quantity_quote: str | None = Field(default=None, max_length=40,
        description='用户原话中表示数量的片段，逐字摘录，如“两束”“3个”')


def resolve_shop(arguments, context):
    shop = arguments.shop or (context.page.shop.shop if context.page.shop else None)
    if shop is None:
        raise shop_reads.ShopReadError('请说明要查询鲜花店还是蛋糕店，或先打开对应店铺。', 'MISSING_SHOP')
    return shop


def selection(context, shop, kind):
    page = context.page.shop
    return page.selected if page and page.shop == shop and page.selected and page.selected.type == kind else None


def envelope(shop, source, data):
    return dict(shop=shop, shop_name=shop_reads.SHOPS[shop]['name'], source=source,
                data_kind='simulated_merchant', currency='CNY', amount_unit='fen', as_of=utc_iso(utc_now()), data=data)


def paginate(items, page, size):
    offset = (page - 1) * size
    return dict(items=items[offset:offset + size], total=len(items), page=page, page_size=size,
                has_more=offset + size < len(items))


async def list_products(arguments, context):
    shop = resolve_shop(arguments, context)
    items = shop_reads.products(shop)
    if arguments.keyword:
        items = [item for item in items if arguments.keyword.casefold() in (item['name'] + ' ' + item['description']).casefold()]
    if arguments.min_price_fen is not None:
        items = [item for item in items if item['price_fen'] >= arguments.min_price_fen]
    if arguments.max_price_fen is not None:
        items = [item for item in items if item['price_fen'] <= arguments.max_price_fen]
    data = paginate(items, arguments.page, arguments.page_size)
    data['filters'] = arguments.model_dump(exclude_none=True, exclude={'page', 'page_size'})
    return envelope(shop, 'shop.products', data)


async def get_product(arguments, context):
    shop = resolve_shop(arguments, context)
    selected = selection(context, shop, 'product')
    key = arguments.product_id or (selected.id if selected else None)
    if key is None:
        raise shop_reads.ShopReadError('请指定商品，或在店铺中选择商品。', 'MISSING_SELECTION')
    item = shop_reads.product(shop, key)
    quantity = arguments.quantity or (selected.quantity if selected and selected.id == key else None) or 1
    return envelope(shop, 'shop.product', dict(product=item, requested_quantity=quantity,
        estimated_total_fen=item['price_fen'] * quantity, quote_only=True))


async def list_orders(arguments, context):
    shop = resolve_shop(arguments, context)
    items = await shop_reads.orders(context.db, context.user_id, shop)
    if arguments.status:
        items = [item for item in items if item['status'] == arguments.status]
    if arguments.keyword:
        items = [item for item in items if arguments.keyword.casefold() in (item['product_name'] + ' ' + item['order_id']).casefold()]
    data = paginate(items, arguments.page, arguments.page_size)
    data['filters'] = arguments.model_dump(exclude_none=True, exclude={'page', 'page_size'})
    return envelope(shop, 'shop.orders', data)


async def get_order(arguments, context):
    shop = resolve_shop(arguments, context)
    selected = selection(context, shop, 'order')
    key = arguments.order_id or (selected.id if selected else None)
    if key is None:
        raise shop_reads.ShopReadError('请指定订单，或在店铺中选择订单。', 'MISSING_SELECTION')
    item = (await shop_reads.orders(context.db, context.user_id, shop, order_id=key))[0]
    return envelope(shop, 'shop.order', item)


SHOP_FIELD_LABELS = {'shop': '店铺', 'product_id': '商品', 'quantity': '数量'}
MAX_CHAT_SHOP_TASKS = 30


def _order_service(shop):
    return shop_reads.SHOPS[shop]['orders']


async def _register_shop_task(context, *, shop, prefill, notice, order_id):
    if not (context.session_id and context.request_id):
        return
    from .card_subscription_tools import _owned_session
    row = await _owned_session(context.db, context.user_id, context.session_id, create=True)
    state = json.loads(json.dumps(row.state or {}))
    entries = list(state.get('shop_tasks', []))
    entries.append({'entry_id': uuid4().hex, 'origin_request_id': context.request_id, 'shop': shop,
                    'prefill': prefill, 'notice': notice, 'order_id': order_id, 'closed': False})
    state['shop_tasks'] = entries[-MAX_CHAT_SHOP_TASKS:]
    row.state = state
    await context.db.commit()


async def prepare_shop_purchase(arguments, context):
    """信息完整时直接下单（只生成银行付款申请，不扣款）；不完整时在聊天中打开预填表单。付款由本人在聊天中确认。"""
    shop = arguments.shop or (context.page.shop.shop if context.page.shop else None)
    message = context.original_message or ''
    prefill, notices = {}, []
    if shop is not None and arguments.product_id is not None:
        try:
            shop_reads.product(shop, arguments.product_id)
            prefill['product_id'] = arguments.product_id
        except shop_reads.ShopReadError:
            notices.append('未找到所说商品，请在表单中选择。')
    elif shop is not None and (selected := selection(context, shop, 'product')) is not None:
        # “这个商品”沿用本人在店铺页选中的商品。
        prefill['product_id'] = selected.id
    # 数量由模型理解，服务端只核验摘录出自用户原话。
    if arguments.quantity is not None:
        if arguments.quantity_quote and arguments.quantity_quote.strip() and arguments.quantity_quote.strip() in message:
            prefill['quantity'] = arguments.quantity
        else:
            notices.append('未能从你的原话中确认数量，请在表单中填写。')
    missing = [key for key, value in (('shop', shop), ('product_id', prefill.get('product_id')),
                                      ('quantity', prefill.get('quantity'))) if value is None]
    notice = ''.join(notices) or None
    order = None
    if not missing and notice is None:
        service = _order_service(shop)
        try:
            await service.create_order(customer_user_id=context.user_id, product_id=prefill['product_id'],
                                       quantity=prefill['quantity'], idempotency_key=f'NOVA-{context.request_id}'[:64])
        except (FlowerShopError, CakeShopError) as error:
            return dict(error=error.code, message=error.message)
        order = (await shop_reads.orders(context.db, context.user_id, shop))[0]
    await _register_shop_task(context, shop=shop, prefill=prefill, notice=notice,
                              order_id=order['order_id'] if order else None)
    if order:
        return envelope(shop, 'shop.order_created', dict(created=True, order=dict(
            order_id=order['order_id'], product_name=order['product_name'], quantity=order['quantity'],
            unit_price_fen=order['unit_price_fen'], total_fen=order['total_fen'], status=order['status']),
            next_step='订单已提交，银行已收到店铺的付款申请，尚未扣款。请在聊天中的订单卡片选择付款账户并由本人确认付款，需要时还须本人输入验证码；NOVA 不能代为付款。'))
    data = dict(created=False, form_opened=True, prefill=prefill, notice=notice,
                missing_fields=[SHOP_FIELD_LABELS[key] for key in missing],
                next_step='尚未下单。请在聊天中的订单表单补全' + '、'.join(SHOP_FIELD_LABELS[key] for key in missing)
                          + '后提交订单，再由本人确认付款。')
    if shop is None:
        return dict(source='shop.order_form', data_kind='simulated_merchant', currency='CNY', amount_unit='fen',
                    as_of=utc_iso(utc_now()), data=data)
    return envelope(shop, 'shop.order_form', data)


async def chat_shop_tasks(db, user_id: str, state: dict) -> list[dict]:
    views = []
    for entry in state.get('shop_tasks', []):
        if entry.get('closed'):
            continue
        order = None
        if entry.get('order_id'):
            try:
                order = (await shop_reads.orders(db, user_id, entry['shop'], order_id=entry['order_id']))[0]
            except shop_reads.ShopReadError:
                continue
        views.append({'entry_id': entry['entry_id'], 'origin_request_id': entry['origin_request_id'],
                      'shop': entry.get('shop'), 'shop_name': shop_reads.SHOPS[entry['shop']]['name'] if entry.get('shop') else None,
                      'prefill': entry['prefill'], 'notice': entry.get('notice'), 'order': order})
    return views


async def update_chat_shop_task(db, *, user_id: str, session_id: str, entry_id: str,
                                shop: str | None = None, order_id: str | None = None, close: bool = False) -> list[dict]:
    """把本人在聊天表单中提交的订单挂到任务卡，或关闭未下单的表单。"""
    from .card_subscription_tools import CardTaskError, _owned_session
    row = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(row.state or {})) if row else {}
    entries = list(state.get('shop_tasks', []))
    entry = next((item for item in entries if item['entry_id'] == entry_id and not item.get('closed')), None)
    if entry is None:
        raise CardTaskError('订单任务不存在', 'SHOP_TASK_NOT_FOUND', 404)
    if close:
        if entry.get('order_id'):
            raise CardTaskError('订单已提交，请在订单卡片中付款或拒绝付款申请', 'SHOP_TASK_ORDERED', 409)
        entry['closed'] = True
    else:
        try:
            await shop_reads.orders(db, user_id, shop, order_id=order_id)
        except (shop_reads.ShopReadError, KeyError):
            raise CardTaskError('订单不存在', 'SHOP_ORDER_NOT_FOUND', 404) from None
        entry.update(shop=shop, order_id=order_id, notice=None)
    state['shop_tasks'] = entries
    row.state = state
    await db.commit()
    return await chat_shop_tasks(db, user_id, state)


def shop_tools():
    entries = [
        ('list_shop_products', ProductListArguments, list_products, '查询模拟鲜花/蛋糕店商品目录，可按关键词和价格范围（整数分）筛选。shop为flower/cake，省略沿用当前店铺，否则须澄清。商品描述来自服务端，不代表库存、配送承诺。'),
        ('get_shop_product', ProductArguments, get_product, '查询商品详情及1-20件只读报价。省略shop/product_id/quantity可继承同店选中商品及数量；estimated_total_fen仅报价，不是下单或付款。ID须来自商品查询或页面选择。'),
        ('list_shop_orders', OrderListArguments, list_orders, '分页查询本人模拟店铺订单及付款状态，按新到旧；可按订单状态或商品名/订单号关键词筛选。shop省略沿用当前店铺。COMPLETED表示银行付款成功，不代表已配送。状态为读取投影，不更新账本。'),
        ('get_shop_order', OrderArguments, get_order, '查询本人具体店铺订单及银行付款状态；省略shop/order_id沿用同店选中订单。未选中须询问，不能猜订单号或查询他人。REFUNDED优先表示整单退款，不把原付款PAID当成未退款。'),
        ('prepare_shop_purchase', PurchaseArguments, prepare_shop_purchase, '用户要在模拟鲜花店/蛋糕店购买商品时调用。先用 list_shop_products 确认商品，再按语义传 shop、product_id 和 quantity，并把原话中的数量说法逐字放入 quantity_quote。未说出的店铺、商品或数量不填、不猜。三项齐全时直接提交订单——这只会让店铺向银行发起付款申请，不扣款；否则聊天中打开预填好的订单表单。付款必须由用户本人在聊天中的订单卡片确认，需要时输入验证码；NOVA 不能代为付款。'),
    ]
    return {name: dict(schema={'type': 'function', 'function': dict(name=name, description=description,
        parameters=model.model_json_schema())}, arguments=model, execute=function, requires_identity=True)
        for name, model, function, description in entries}


async def offline_reply(message, context, execute):
    if context is None or re.search(r'帮我买|下单|支付这|立即支付|取消订单|申请退款|退掉|购买', message):
        return None
    shops = set()
    if re.search(r'蛋糕|草莓|巧克力', message):
        shops.add('cake')
    if re.search(r'鲜花|花店|玫瑰|向日葵|花束', message):
        shops.add('flower')
    page = context.page.shop
    if not shops and re.search(r'余额|可用资金|账单|日历|日程|安排|规划|活动', message) and not re.search(r'这个商品|这款|这个订单|这笔订单', message):
        return None
    if not shops and page:
        shops.add(page.shop)
    if not shops:
        return None
    if len(shops) != 1:
        return dict(reply='请先指定要查询蛋糕店还是鲜花店。两家店的比较可在模型模式下查询。', mode='offline', tool_calls=[])
    shop = shops.pop()
    arguments = {'shop': shop}
    if re.search(r'这个订单|这笔订单|选中订单', message):
        name = 'get_shop_order'
    elif re.search(r'订单|付款了吗|付了吗|退款了吗', message):
        name = 'list_shop_orders'
        for text, status in [('部分退款','PARTIALLY_REFUNDED'),('待付款', 'PENDING_PAYMENT'), ('已付款', 'COMPLETED'), ('已退款', 'REFUNDED'), ('已关闭', 'PAYMENT_CLOSED')]:
            if text in message:
                arguments['status'] = status
    elif re.search(r'这个商品|这款|选中商品', message):
        name = 'get_shop_product'
    elif re.search(r'商品|有哪些|多少钱|价格|介绍|卖什么', message):
        name = 'list_shop_products'
    else:
        return None
    result = await execute({'function': {'name': name, 'arguments': json.dumps(arguments)}}, context)
    if result.get('error'):
        reply = result.get('message', '店铺查询暂时无法完成。')
    else:
        data = result['data']
        if name == 'get_shop_product':
            item = data['product']
            reply = f"{result['shop_name']}：{item['name']}，单价 ¥{item['price_fen']/100:,.2f}。{item['description']}\n{data['requested_quantity']}件报价 ¥{data['estimated_total_fen']/100:,.2f}（未下单）。"
        elif name == 'list_shop_products':
            reply = f"{result['shop_name']}共有{data['total']}款符合条件的商品：\n" + '\n'.join(f"- {i['name']} · ¥{i['price_fen']/100:,.2f} · {i['description']}" for i in data['items'])
        else:
            items = data['items'] if name == 'list_shop_orders' else [data]
            labels = {'PENDING_PAYMENT': '等待付款', 'COMPLETED': '交易完成', 'PAYMENT_CLOSED': '付款已关闭', 'REFUNDED': '已退款','PARTIALLY_REFUNDED':'部分退款'}
            reply = f"{result['shop_name']}本人订单：\n" + ('暂无符合条件的订单。' if not items else '\n'.join(
                f"- {i['order_id']} · {i['product_name']} × {i['quantity']} · ¥{i['total_fen']/100:,.2f} · {labels[i['status']]} · 银行付款状态：{i['payment_request']['status'] if i['payment_request'] else '未取得'}" for i in items))
            if name == 'list_shop_orders' and data['has_more']:
                reply += '\n还有更多订单，当前仅显示本页。'
        reply += '\n以上为模拟店铺信息。'
    return dict(reply=reply, mode='offline', tool_calls=[dict(name=name, result=result)])
