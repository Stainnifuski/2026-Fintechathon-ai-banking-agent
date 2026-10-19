"""模拟花店/蛋糕店只读投影；订单价格用下单快照，付款事实由银行读取。"""
from sqlalchemy import select
from ..domain.time import as_utc

from ..models.flower_order import FlowerOrderORM
from ..models.cake_order import CakeOrderORM
from .flower_shop import catalog as flower_catalog, order_service as flower_orders
from .cake_shop import catalog as cake_catalog, order_service as cake_orders
from .bank.payment_request_service import read_payment_snapshots

SHOPS = {
    'flower': dict(model=FlowerOrderORM, catalog=flower_catalog, orders=flower_orders,
                   merchant_id=flower_orders.FLOWER_MERCHANT_USER_ID, name=flower_orders.FLOWER_MERCHANT_NAME),
    'cake': dict(model=CakeOrderORM, catalog=cake_catalog, orders=cake_orders,
                 merchant_id=cake_orders.CAKE_MERCHANT_USER_ID, name=cake_orders.CAKE_MERCHANT_NAME),
}


class ShopReadError(Exception):
    def __init__(self, message, code):
        self.message, self.code = message, code
        super().__init__(message)


def products(shop):
    return [{key: value for key, value in item.items() if key not in ('price', 'image_key')}
            for item in SHOPS[shop]['catalog'].list_products()]


def product(shop, product_id):
    item = next((item for item in products(shop) if item['product_id'] == product_id), None)
    if item is None:
        raise ShopReadError('当前店铺商品不存在或已下架', 'PRODUCT_NOT_FOUND')
    return item


async def orders(db, user_id, shop, *, order_id=None):
    config = SHOPS[shop]
    model = config['model']
    statement = select(model).where(model.customer_user_id == user_id)
    if order_id is not None:
        statement = statement.where(model.order_id == order_id)
    rows = (await db.scalars(statement.order_by(model.created_at.desc(), model.order_id.desc()))).all()
    if order_id is not None and not rows:
        raise ShopReadError('订单不存在或无权访问', 'ORDER_NOT_FOUND')
    payments = await read_payment_snapshots(db, user_id=user_id, requester_user_id=config['merchant_id'],
        payment_request_ids=[row.payment_request_id for row in rows if row.payment_request_id])
    result = []
    for row in rows:
        payment = payments.get(row.payment_request_id)
        raw = config['orders']._view(row)
        view = {key: raw[key] for key in ('order_id', 'product_id', 'product_name', 'product_description',
            'quantity', 'unit_price_fen', 'total_fen', 'currency', 'payment_request_id',
            'status', 'state_version', 'created_at', 'updated_at')}
        view['created_at'] = as_utc(row.created_at, legacy_utc=True)
        view['updated_at'] = as_utc(row.updated_at, legacy_utc=True)
        view['refunded_fen']=(payment or {}).get('refunded_fen',row.total_fen if row.status=='REFUNDED' else 0)
        view['remaining_refundable_fen']=max(0,row.total_fen-view['refunded_fen'])
        view['stored_status'] = row.status
        view['status'] = config['orders'].effective_order_status(row.status, payment)
        view['status_projected'] = view['status'] != row.status
        view['payment_request'] = payment
        view['payment_status_available'] = payment is not None
        result.append(view)
    return result
