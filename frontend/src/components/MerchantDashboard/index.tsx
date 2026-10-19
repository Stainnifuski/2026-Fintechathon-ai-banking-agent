import { beijingTime } from '../../utils/time';
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Account, Transaction, User } from '../../services/bank';
import * as flowerShop from '../../services/flowerShop';
import * as cakeShop from '../../services/cakeShop';
import './styles.css';

interface Props {
  user: User;
  accounts: Account[];
  transactions: Transaction[];
  revision: number;
  onRefresh: () => Promise<void>;
  onLogout: () => void;
}

const errorMessage = (error: unknown) => {
  const detail = (error as {
    response?: { data?: { detail?: string | { message?: string } } };
  })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
};

const orderStatus: Record<flowerShop.FlowerOrder['status'], string> = {
  PENDING_PAYMENT: '待付款',
  COMPLETED: '已收款',
  PAYMENT_CLOSED: '付款关闭',
  REFUNDED: '已退款',
  PARTIALLY_REFUNDED: '部分退款',
};

export default function MerchantDashboard({
  user,
  accounts,
  transactions,
  revision,
  onRefresh,
  onLogout,
}: Props) {
  const shop = user.user_id === 'U008' ? cakeShop : flowerShop;
  const category = user.user_id === 'U008' ? '蛋糕' : '鲜花';
  const [orders, setOrders] = useState<flowerShop.FlowerOrder[]>([]);
  const [pendingRefund, setPendingRefund] = useState<flowerShop.FlowerOrder | null>(null);
  const [refundAmount,setRefundAmount]=useState('');
  const [refundOperation,setRefundOperation]=useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const loadOrders = useCallback(async () => {
    try {
      setOrders(await shop.merchantOrders());
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }, [shop]);

  useEffect(() => { loadOrders(); }, [loadOrders, revision]);

  const totals = useMemo(() => ({
    paid: orders.filter(order => ['COMPLETED','PARTIALLY_REFUNDED'].includes(order.status)).reduce((sum, order) => sum + (order.total_fen-order.refunded_fen)/100, 0),
    refunded: orders.reduce((sum,order)=>sum+(order.refunded_fen||0)/100,0),
    pending: orders.filter(order => order.status === 'PENDING_PAYMENT').length,
  }), [orders]);

  const confirmRefund = async () => {
    if (!pendingRefund || busy) return;
    setBusy(true);
    setNotice('');
    try {
      const fen=Math.round(Number(refundAmount)*100);
      if(!Number.isFinite(fen)||fen<=0||fen>pendingRefund.remaining_refundable_fen)throw new Error('请输入不超过剩余可退金额的退款数额');
      const updated = await shop.refundOrder(pendingRefund,fen,refundOperation);
      setOrders(items => items.map(item => item.order_id === updated.order_id ? updated : item));
      setPendingRefund(null);
      setNotice(`订单 ${updated.order_id} 已退款 ¥${(fen/100).toFixed(2)}，累计已退 ¥${(updated.refunded_fen/100).toFixed(2)}。`);
      await onRefresh();
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    try {
      await Promise.all([onRefresh(), loadOrders()]);
    } finally {
      setBusy(false);
    }
  };

  const account = accounts[0];

  return <main className="merchant-app">
    <header className="merchant-header">
      <div><p className="merchant-kicker">商户工作台</p><strong>{user.name}</strong><span>{category}订单与资金管理</span></div>
      <div className="confirm"><button className="secondary" disabled={busy} onClick={refresh}>刷新数据</button><button className="secondary" onClick={onLogout}>退出登录</button></div>
    </header>

    {notice && <p className="notice">{notice}</p>}

    <section className="merchant-summary">
      <article className="merchant-balance">
        <span>商户账户余额</span>
        <strong>¥{account?.balance.toFixed(2) ?? '0.00'}</strong>
        <small>{account?.card_number ?? '账户加载中'} · 可用 ¥{((account?.available_fen ?? 0) / 100).toFixed(2)}</small>
      </article>
      <article><span>当前已收款订单</span><strong>¥{totals.paid.toFixed(2)}</strong><small>不含已退款订单</small></article>
      <article><span>累计退款</span><strong>¥{totals.refunded.toFixed(2)}</strong><small>原路退回顾客付款账户</small></article>
      <article><span>待付款订单</span><strong>{totals.pending}</strong><small>等待顾客在银行确认</small></article>
    </section>

    <section className="merchant-grid">
      <div className="card merchant-orders">
        <div className="section-heading"><div><p className="merchant-kicker">订单</p><h2>{category}订单</h2></div></div>
        {orders.length === 0 ? <p className="hint">暂无订单</p> : <ul>
          {orders.map(order => <li key={order.order_id}>
            <div>
              <strong>{order.product_name} × {order.quantity}</strong>
              <small>{order.payment_request?.payer_name || order.customer_user_id} · {order.order_id}</small>
              <small>{beijingTime(order.created_at)}</small>
            </div>
            <div className="merchant-order-action">
              <strong>¥{order.total.toFixed(2)}</strong>
              <span className={`merchant-status ${order.status.toLowerCase()}`}>{orderStatus[order.status]}</span>
              {['COMPLETED','PARTIALLY_REFUNDED'].includes(order.status) && <><small>已退 ¥{((order.refunded_fen||0)/100).toFixed(2)} · 剩余可退 ¥{((order.remaining_refundable_fen||0)/100).toFixed(2)}</small><button className="secondary" disabled={busy} onClick={() => {setPendingRefund(order);setRefundAmount(String(order.remaining_refundable_fen/100));setRefundOperation(crypto.randomUUID());}}>退款…</button></>}
            </div>
          </li>)}
        </ul>}
      </div>

      <div className="card merchant-transactions">
        <p className="merchant-kicker">账户</p><h2>最近流水</h2>
        {transactions.length === 0 ? <p className="hint">暂无商户流水</p> : <ul>
          {transactions.map(transaction => {
            const outgoing = transaction.type === 'refund_out';
            const label = transaction.type === 'merchant_sale' ? '订单收款' : outgoing ? '订单退款' : transaction.type;
            return <li key={transaction.tx_id}>
              <div><strong>{label} · {transaction.counterparty}</strong><small>{transaction.note || '无备注'}</small><small>{beijingTime(transaction.timestamp)}</small></div>
              <strong className={outgoing ? 'out' : 'in'}>{outgoing ? '-' : '+'}¥{transaction.amount.toFixed(2)}</strong>
            </li>;
          })}
        </ul>}
      </div>
    </section>

    {pendingRefund && <div className="merchant-modal" role="dialog" aria-modal="true" aria-label="确认退款">
      <section className="card">
        <p className="merchant-kicker">退款确认</p>
        <h2>向 {pendingRefund.payment_request?.payer_name || '顾客'} 退款？</h2>
        <p>订单：{pendingRefund.product_name} × {pendingRefund.quantity}</p>
        <p>原付款 ¥{pendingRefund.total.toFixed(2)} · 已退 ¥{(pendingRefund.refunded_fen/100).toFixed(2)} · 剩余可退 ¥{(pendingRefund.remaining_refundable_fen/100).toFixed(2)}</p><label>本次退款金额（元）<input aria-label="本次退款金额" disabled={busy} type="number" min="0.01" step="0.01" max={pendingRefund.remaining_refundable_fen/100} value={refundAmount} onChange={e=>{setRefundAmount(e.target.value);setRefundOperation(crypto.randomUUID());}} /></label>
        <p className="hint">确认后银行将从商户账户原路退回顾客付款账户，并生成双方退款流水。支持多次部分退款，累计不超过原付款。按原操作重试不会重复记账。</p>
        <div className="confirm"><button className="secondary" disabled={busy} onClick={() => setPendingRefund(null)}>返回</button><button disabled={busy} onClick={confirmRefund}>{busy ? '退款处理中…' : '确认退款'}</button></div>
      </section>
    </div>}
  </main>;
}
