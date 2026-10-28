import {useTaskField,useTaskPrefill} from './NovaTaskPresentation';
import { useEffect, useState, type FormEvent } from 'react';
import * as bank from '../services/bank';
import * as cakeShop from '../services/cakeShop';
import * as flowerShop from '../services/flowerShop';
import { dismissNovaShopTask, linkNovaShopTask, type NovaChatShopTask } from '../services/nova';
import { SecondFactor } from './SecondFactor';
import { formatFen } from '../utils/money';

type ShopKey = 'flower' | 'cake';
type Product = { product_id: string; name: string; description: string; price_fen: number };
const SHOP_API = { flower: flowerShop, cake: cakeShop };
const SHOP_NAMES: Record<ShopKey, string> = { flower: '鲜花店', cake: '蛋糕店' };
const ORDER_STATUS: Record<string, string> = {
  PENDING_PAYMENT: '等待本人付款', COMPLETED: '付款成功', PAYMENT_CLOSED: '付款已关闭',
  REFUNDED: '已退款', PARTIALLY_REFUNDED: '部分退款',
};
const RISK: Record<string, string> = { green: '低风险', yellow: '需本人确认', red: '需确认 + 验证码' };
const yuan = (fen: number) => formatFen(fen);
const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const errorText = (err: unknown) => {
  const detail = (err as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return err instanceof Error ? err.message : '请求失败，请稍后重试';
};

/** NOVA 下单的店铺订单：信息不全时补全下单；付款由本人在卡片中选择账户、确认并验证。 */
export default function NovaShopCard({ task, sessionId, accounts, disabled, onUpdated, onActivity }: {
  task: NovaChatShopTask; sessionId: string; accounts: bank.Account[]; disabled: boolean;
  onUpdated: (tasks: NovaChatShopTask[]) => void; onActivity?: () => void;
}) {
  const [shop, setShop] = useTaskField<ShopKey | ''>('shop',task.shop ?? '');
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useTaskField('productId',task.prefill.product_id ?? '');
  const [quantity, setQuantity] = useTaskField('quantity',task.prefill.quantity ? String(task.prefill.quantity) : '');
  const [order, setOrder] = useState(task.order ?? null);
  const payable = accounts.filter(a => ['savings', 'checking'].includes(a.account_type));
  const [accountId, setAccountId] = useTaskField('accountId','');
  const [request, setRequest] = useState<bank.PaymentRequest | null>(null);
  const [payment, setPayment] = useState<bank.TaskSnapshot | null>(null);
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [code, setCode] = useState('');
  useEffect(()=>{const clear=()=>setCode('');window.addEventListener('nova-clear-verification',clear);return()=>window.removeEventListener('nova-clear-verification',clear);},[]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(task.notice ?? '');
  useTaskPrefill({...task.prefill,shop:task.shop},{product_id:v=>setProductId(String(v??'')),quantity:v=>setQuantity(String(v??'')),shop:v=>{if(v==='flower'||v==='cake')setShop(v);}});

  useEffect(() => { setOrder(task.order ?? null); }, [task.order?.order_id, task.order?.status]);
  useEffect(() => { if (!accountId && payable[0]) setAccountId(payable[0].account_id); }, [accounts]);
  // 表单阶段读取所选店铺的商品目录。
  useEffect(() => {
    if (order || !shop) return;
    let live = true;
    SHOP_API[shop].products().then(items => { if (live) setProducts(items); }).catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [shop, order?.order_id]);
  // 待付款订单读取银行付款申请，付款以银行状态为准。
  useEffect(() => {
    if (!order?.payment_request_id || order.status !== 'PENDING_PAYMENT') return;
    let live = true;
    bank.paymentRequests().then(items => { if (live) setRequest(items.find(item => item.payment_request_id === order.payment_request_id) ?? null); })
      .catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [order?.payment_request_id, order?.status]);

  const off = busy || disabled;
  const product = products.find(item => item.product_id === productId);
  const count = Number(quantity);
  const validCount = Number.isInteger(count) && count >= 1 && count <= 20;

  const act = async (step: () => Promise<void>) => {
    if (off) return;
    setBusy(true); setError('');
    try { await step(); } catch (err) { setError(errorText(err)); setCode(''); } finally { setBusy(false); }
  };
  const refreshOrder = async () => {
    if (!order || !task.shop) return;
    const latest = await SHOP_API[task.shop].order(order.order_id);
    setOrder({ ...order, status: latest.status });
  };

  const submitOrder = (event: FormEvent) => {
    event.preventDefault();
    void act(async () => {
      if (!shop || !product || !validCount) return;
      const created = await SHOP_API[shop].createOrder({ product_id: product.product_id, quantity: count, idempotency_key: newId() });
      onUpdated(await linkNovaShopTask(sessionId, task.entry_id, shop, created.order_id));
      setOrder({ order_id: created.order_id, product_id: created.product_id, product_name: created.product_name,
        quantity: created.quantity, unit_price_fen: created.unit_price_fen, total_fen: created.total_fen,
        status: created.status, payment_request_id: created.payment_request_id });
    });
  };
  const advance = async (next: bank.TaskSnapshot) => {
    // 与银行页一致：本人授权后由银行受理；需要强验证时停在验证步骤。
    if (next.allowed_actions.includes('accept')) next = await bank.acceptTask(next, newId());
    setPayment(next); setCode('');
    if (next.phase === 'FINISHED') { await refreshOrder(); onActivity?.(); }
  };
  const startPayment = () => act(async () => {
    if (!request || !accountId) return;
    setPayment(await bank.acceptPaymentRequest(request, accountId));
  });
  const confirmPayment = () => act(async () => { await advance(await bank.confirmTask(payment!, newId())); });
  const verifyPayment = () => act(async () => { if (factor) await advance(await bank.verifyTask(payment!, newId(), code, factor)); });
  const cancelPayment = () => act(async () => { setPayment(await bank.cancelTask(payment!, newId())); });
  const declineOrder = () => act(async () => {
    if (!request) return;
    await bank.rejectPaymentRequest(request); setRequest(null); await refreshOrder();
  });
  const dismiss = () => act(async () => { onUpdated(await dismissNovaShopTask(sessionId, task.entry_id)); });

  const allowed = payment?.allowed_actions ?? [];
  const shopName = (task.shop && SHOP_NAMES[task.shop]) || (shop && SHOP_NAMES[shop]) || '店铺';
  return <article className="card nova-transfer-card nova-shop-card" aria-label="店铺订单">
    <div className="nova-transfer-card-head"><div>
      <strong>{order ? `${shopName}订单 · ${order.product_name} × ${order.quantity}` : `${shopName}下单`}</strong>
      <small>{order ? ORDER_STATUS[order.status] ?? order.status : '填写后提交订单 · 尚未下单'}</small>
    </div></div>

    {!order && <form className="nova-collection-form" onSubmit={submitOrder}>
      {!task.shop && <label>店铺<select value={shop} disabled={off} onChange={e => { setShop(e.target.value as ShopKey); setProductId(''); }}>
        <option value="">请选择店铺</option><option value="flower">鲜花店</option><option value="cake">蛋糕店</option></select></label>}
      <label>商品<select value={productId} disabled={off || !shop} onChange={e => setProductId(e.target.value)}>
        <option value="">{shop ? '请选择商品' : '请先选择店铺'}</option>
        {products.map(item => <option key={item.product_id} value={item.product_id}>{item.name} · {yuan(item.price_fen)}</option>)}</select></label>
      {product && <p className="hint">{product.description}</p>}
      <label>数量（1-20）<input type="number" min={1} max={20} step={1} value={quantity} disabled={off} placeholder="请输入数量" onChange={e => setQuantity(e.target.value)} /></label>
      {product && validCount && <p>合计 <strong>{yuan(product.price_fen * count)}</strong></p>}
      <p className="hint">提交订单后，店铺会向银行发起付款申请，此时不会扣款；付款需你在本卡片中确认。</p>
      <div className="nova-transfer-actions">
        <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>不购买</button>
        <button type="submit" disabled={off || !product || !validCount}>{busy ? '正在提交…' : '提交订单'}</button>
      </div>
    </form>}

    {order && <>
      <ul className="history">
        <li><span>单价</span><span>{yuan(order.unit_price_fen)}</span></li>
        <li><span>合计</span><span><strong>{yuan(order.total_fen)}</strong></span></li>
      </ul>
      {order.status === 'PENDING_PAYMENT' && !payment && request && ['PENDING', 'ACCEPTED'].includes(request.status) && <div className="nova-transfer-review">
        <label>付款账户<select value={accountId} disabled={off} onChange={e => setAccountId(e.target.value)}>
          {payable.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>
        <p className="hint">收款方：{request.requester_name}。下一步由银行生成付款单并评估风险，尚不会扣款。</p>
        <div className="nova-transfer-actions">
          <button type="button" className="secondary" disabled={off} onClick={() => void declineOrder()}>不付款</button>
          <button type="button" disabled={off || !accountId} onClick={() => void startPayment()}>{busy ? '正在生成…' : '去付款'}</button>
        </div>
      </div>}
      {payment && payment.phase !== 'FINISHED' && <div className="nova-transfer-review">
        <p>{payment.message}</p>
        <p>付款金额 <strong>{yuan(payment.operation.amount_fen)}</strong> · {RISK[payment.authorization.risk_level] ?? payment.authorization.risk_level}</p>
        {allowed.includes('verify') && <div className="nova-transfer-verify">
          <SecondFactor purpose="transfer" referenceId={payment.task_id} factor={factor} onChange={setFactor} disabled={off} />
          <input inputMode="numeric" maxLength={6} value={code} placeholder="6 位验证码" disabled={off}
            onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
        </div>}
        <small className="hint">NOVA 不能代为确认付款或输入验证码。</small>
        <div className="nova-transfer-actions">
          {allowed.includes('cancel') && <button type="button" className="secondary" disabled={off} onClick={() => void cancelPayment()}>取消付款</button>}
          {allowed.includes('confirm') && <button type="button" disabled={off} onClick={() => void confirmPayment()}>{busy ? '处理中…' : '确认付款'}</button>}
          {allowed.includes('verify') && <button type="button" disabled={off || !factor || code.length !== 6} onClick={() => void verifyPayment()}>{busy ? '验证中…' : '提交验证'}</button>}
        </div>
      </div>}
      {payment?.phase === 'FINISHED' && <p>{payment.message}</p>}
    </>}
    {error && <p className={order ? 'error' : 'hint'} role="alert">{error}</p>}
  </article>;
}
