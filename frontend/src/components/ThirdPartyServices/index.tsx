import { useCallback, useEffect, useState } from 'react';
import * as flowerShop from '../../services/flowerShop';
import * as cakeShop from '../../services/cakeShop';
import Calendar from '../Calendar';
import './styles.css';

type ServiceTab = 'calendar' | 'flower' | 'cake';

interface Props {
  revision: number;
  onBack: () => void;
  onGoBank: (paymentRequestId?: string) => void;
}

const newId = () => crypto.randomUUID?.() ?? `shop-${Date.now()}-${Math.random()}`;

const errorMessage = (error: unknown) => {
  const detail = (error as {
    response?: { data?: { detail?: string | { message?: string } } };
  })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return error instanceof Error ? error.message : '商店服务暂时不可用';
};

const statusText: Record<flowerShop.FlowerOrder['status'], string> = {
  PENDING_PAYMENT: '等待银行付款',
  COMPLETED: '交易完成',
  PAYMENT_CLOSED: '付款已关闭',
  REFUNDED: '已退款',
};

export default function ThirdPartyServices({ revision, onBack, onGoBank }: Props) {
  const [tab, setTab] = useState<ServiceTab>('flower');
  return <section className="third-party">
    <div className="third-party-title">
      <div>
        <p className="eyebrow">生活场景联动</p>
        <h1>第三方服务</h1>
        <p>安排日程，选购心意。店铺付款由银行在您明确同意后执行。</p>
      </div>
      <button className="secondary" onClick={onBack}>返回银行</button>
    </div>
    <nav aria-label="第三方服务导航">
      {([['calendar', '日历'], ['flower', '花店'], ['cake', '蛋糕店']] as [ServiceTab, string][]).map(([key, label]) =>
        <button key={key} className={tab === key ? '' : 'secondary'} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>
      )}
    </nav>
    {tab === 'calendar' ? <Calendar revision={revision} /> : <Storefront key={tab} shopType={tab} revision={revision} onGoBank={onGoBank} />}
  </section>;
}

function Storefront({ revision, onGoBank, shopType }: Pick<Props, 'revision' | 'onGoBank'> & { shopType: 'flower' | 'cake' }) {
  const shop = shopType === 'cake' ? cakeShop : flowerShop;
  const category = shopType === 'cake' ? '蛋糕' : '鲜花';
  const shopName = shopType === 'cake' ? '甜时光蛋糕店' : '花语鲜花店';
  const [products, setProducts] = useState<flowerShop.FlowerProduct[]>([]);
  const [orders, setOrders] = useState<flowerShop.FlowerOrder[]>([]);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [pendingProduct, setPendingProduct] = useState<flowerShop.FlowerProduct | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      const [productItems, orderItems] = await Promise.all([
        shop.products(),
        shop.orders(),
      ]);
      setProducts(productItems);
      setOrders(orderItems);
      setQuantities(current => {
        const next = { ...current };
        productItems.forEach(product => { next[product.product_id] ??= 1; });
        return next;
      });
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setIsLoading(false);
    }
  }, [shop]);

  useEffect(() => { load(); }, [load, revision]);
  useEffect(() => {
    if (!orders.some(order => order.status === 'PENDING_PAYMENT')) return;
    const timer = window.setInterval(load, 4000);
    return () => window.clearInterval(timer);
  }, [load, orders]);

  const openConfirmation = (product: flowerShop.FlowerProduct) => {
    setPendingProduct(product);
    setIdempotencyKey(newId());
    setNotice('');
  };

  const submitOrder = async () => {
    if (!pendingProduct || !idempotencyKey || isSubmitting) return;
    setIsSubmitting(true);
    try {
      const created = await shop.createOrder({
        product_id: pendingProduct.product_id,
        quantity: quantities[pendingProduct.product_id] || 1,
        idempotency_key: idempotencyKey,
      });
      setOrders(current => [created, ...current.filter(item => item.order_id !== created.order_id)]);
      setPendingProduct(null);
      setIdempotencyKey('');
      setNotice(`订单已创建，${shopName}已向银行提交付款申请。请前往银行核对并明确确认。`);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  const latestPending = orders.find(order => order.status === 'PENDING_PAYMENT');

  return <section className={`storefront ${shopType}-shop`}>
    {notice && <div className="third-party-notice">
      <span>{notice}</span>
      {latestPending?.payment_request_id &&
        <button onClick={() => onGoBank(latestPending.payment_request_id)}>前往银行确认</button>}
    </div>}

      <div className="flower-hero card">
        <div>
          <p className="eyebrow">{shopName}</p>
          <h2>{shopType === 'cake' ? '让每个重要时刻，都多一点甜' : '把心意送到重要的人身边'}</h2>
          <p>选择{category}并提交订单。{shopName}会通过银行向您发起一次性收款申请。</p>
        </div>
        <div className="flower-hero-mark" aria-hidden="true">{shopType === 'cake' ? 'CAKE' : 'FLOWER'}</div>
      </div>

      {isLoading ? <div className="card"><p>正在加载{shopName}…</p></div> :
        <div className="flower-products">
          {products.map(product => {
            const quantity = quantities[product.product_id] || 1;
            return <article className="flower-product card" key={product.product_id}>
              <div className={`flower-art ${product.image_key}`} aria-hidden="true">
                <span>{({ rose: 'ROSE', sunflower: 'SUN', premium: 'GIFT', strawberry: 'BERRY', chocolate: 'COCOA', celebration: 'PARTY' } as Record<string, string>)[product.image_key] || 'GIFT'}</span>
              </div>
              <div className="flower-product-body">
                <h2>{product.name}</h2>
                <p>{product.description}</p>
                <strong>¥{product.price.toFixed(2)}</strong>
                <label>数量
                  <select value={quantity} onChange={event => setQuantities(current => ({
                    ...current,
                    [product.product_id]: Number(event.target.value),
                  }))}>
                    {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <button onClick={() => openConfirmation(product)}>购买{category}</button>
              </div>
            </article>;
          })}
        </div>}

      <section className="card flower-orders">
        <div className="section-heading">
          <div><p className="eyebrow">订单记录</p><h2>我的{category}订单</h2></div>
          <button className="secondary" onClick={load}>刷新状态</button>
        </div>
        {orders.length === 0 ? <p className="hint">还没有{category}订单。</p> :
          <ul>
            {orders.map(order => <li key={order.order_id}>
              <div>
                <strong>{order.product_name} × {order.quantity}</strong>
                <small>{order.order_id} · {new Date(order.created_at).toLocaleString()}</small>
              </div>
              <div className="order-summary">
                <strong>¥{order.total.toFixed(2)}</strong>
                <span className={`order-status ${order.status.toLowerCase()}`}>
                  {statusText[order.status]}
                </span>
                {order.status === 'PENDING_PAYMENT' && order.payment_request_id &&
                  <button onClick={() => onGoBank(order.payment_request_id)}>前往付款</button>}
              </div>
            </li>)}
          </ul>}
      </section>

    {pendingProduct && <div className="flower-modal" role="dialog" aria-modal="true" aria-label={`确认${category}订单`}>
      <section className="card">
        <p className="eyebrow">订单确认</p>
        <h2>{pendingProduct.name}</h2>
        <dl>
          <div><dt>单价</dt><dd>¥{pendingProduct.price.toFixed(2)}</dd></div>
          <div><dt>数量</dt><dd>{quantities[pendingProduct.product_id] || 1}</dd></div>
          <div><dt>订单总额</dt><dd><strong>¥{(pendingProduct.price * (quantities[pendingProduct.product_id] || 1)).toFixed(2)}</strong></dd></div>
        </dl>
        <p className="hint">提交订单仅生成银行付款申请，不代表您已同意扣款。请随后返回银行核对收款人、金额和风险等级。</p>
        <div className="confirm">
          <button className="secondary" disabled={isSubmitting} onClick={() => setPendingProduct(null)}>返回</button>
          <button disabled={isSubmitting} onClick={submitOrder}>{isSubmitting ? '正在提交…' : '提交订单'}</button>
        </div>
      </section>
    </div>}
  </section>;
}
