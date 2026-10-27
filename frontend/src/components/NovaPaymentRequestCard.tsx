import { useState } from 'react';
import * as bank from '../services/bank';
import { getNovaTaskSession, NovaPaymentRequestTask, NovaTaskSession } from '../services/nova';
import { beijingTime } from '../utils/time';
import { cardTypeLabel } from '../utils/accountType';
import { SecondFactor } from './SecondFactor';

type Props = {
  task: NovaPaymentRequestTask; sessionId: string; accounts: bank.Account[];
  disabled: boolean; onUpdated: (session: NovaTaskSession) => void; onActivity?: () => void;
};

function message(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : detail?.message ||
    (error instanceof Error ? error.message : '操作失败，请刷新后重试。');
}

export default function NovaPaymentRequestCard({ task, sessionId, accounts, disabled, onUpdated, onActivity }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [payerAccount, setPayerAccount] = useState(accounts.find(a => a.account_type === 'savings')?.account_id || accounts[0]?.account_id || '');
  const [paymentTask, setPaymentTask] = useState<bank.TaskSnapshot | null>(null);
  const [paymentRequestId, setPaymentRequestId] = useState<string | null>(null);
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [code, setCode] = useState('');
  const pending = task.requests.filter(request => request.status === 'PENDING');
  const actionable = task.action === 'cancel' ? pending : task.requests.filter(request => ['PENDING', 'ACCEPTED'].includes(request.status));

  async function refresh() {
    onUpdated(await getNovaTaskSession(sessionId));
    onActivity?.();
  }

  async function act(request: bank.PaymentRequest, action: 'cancel' | 'reject') {
    if (busy || disabled) return;
    setBusy(true); setError(''); setNotice('');
    try {
      if (action === 'cancel') await bank.cancelPaymentRequest(request);
      else await bank.rejectPaymentRequest(request);
      setNotice(action === 'cancel' ? '已取消这笔收款申请。' : '已拒绝这笔收款申请。');
      await refresh();
    } catch (failure) { setError(message(failure)); await refresh().catch(() => {}); }
    finally { setBusy(false); }
  }

  async function cancelAll() {
    if (busy || disabled || pending.length < 2) return;
    setBusy(true); setError(''); setNotice('');
    let completed = 0;
    const failures: string[] = [];
    for (const request of pending) {
      try { await bank.cancelPaymentRequest(request); completed += 1; }
      catch (failure) { failures.push(`${request.payer_name}：${message(failure)}`); }
    }
    setNotice(`已取消 ${completed} 笔收款申请。`);
    if (failures.length) setError(failures.join('；'));
    await refresh().catch(failure => setError(message(failure)));
    setBusy(false);
  }

  async function prepare(request: bank.PaymentRequest) {
    if (busy || disabled || !payerAccount) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const next = await bank.acceptPaymentRequest(request, payerAccount);
      setPaymentRequestId(request.payment_request_id);
      setPaymentTask(next);
      setNotice('已生成付款计划，请核对收款人和金额后点击确认。');
      await refresh();
    } catch (failure) { setError(message(failure)); await refresh().catch(() => {}); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!paymentTask || busy || disabled) return;
    setBusy(true); setError('');
    try {
      const latest = await bank.getTask(paymentTask.task_id);
      if (latest.state_version !== paymentTask.state_version || latest.plan_version !== paymentTask.plan_version) {
        setPaymentTask(latest); setNotice('付款计划已更新，请重新核对后确认。'); return;
      }
      let next = await bank.confirmTask(latest, crypto.randomUUID());
      if (next.allowed_actions.includes('accept')) next = await bank.acceptTask(next, crypto.randomUUID());
      setPaymentTask(next); setNotice(next.message);
      await refresh();
    } catch (failure) {
      setError(message(failure));
      const latest = await bank.getTask(paymentTask.task_id).catch(() => null);
      if (latest) setPaymentTask(latest);
    } finally { setBusy(false); }
  }

  async function verify() {
    if (!paymentTask || !factor || code.length !== 6 || busy || disabled) return;
    setBusy(true); setError('');
    try {
      let next = await bank.getTask(paymentTask.task_id);
      if (next.state_version !== paymentTask.state_version || next.plan_version !== paymentTask.plan_version) {
        setPaymentTask(next); setNotice('付款计划已更新，请重新核对。'); return;
      }
      next = await bank.verifyTask(next, crypto.randomUUID(), code, factor);
      if (next.allowed_actions.includes('accept')) next = await bank.acceptTask(next, crypto.randomUUID());
      setPaymentTask(next); setCode(''); setNotice(next.message);
      await refresh();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  async function cancelPaymentPlan() {
    if (!paymentTask || busy || disabled) return;
    setBusy(true); setError('');
    try {
      const latest = await bank.getTask(paymentTask.task_id);
      const next = await bank.cancelTask(latest, crypto.randomUUID());
      setPaymentTask(null); setPaymentRequestId(null); setNotice(next.message);
      await refresh();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  async function reconcilePayment() {
    if (!paymentTask || busy || disabled) return;
    setBusy(true); setError('');
    try {
      const latest = await bank.getTask(paymentTask.task_id);
      const next = await bank.reconcileTask(latest, crypto.randomUUID());
      setPaymentTask(next); setNotice(next.message);
      await refresh();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  return <article className="card nova-transfer-card nova-collection-card" aria-label={task.action === 'cancel' ? '取消收款申请任务' : '处理收到的收款申请'}>
    <div className="nova-transfer-card-head"><strong>{task.action === 'cancel' ? '取消收款申请' : '收到的收款申请'}</strong>
      <small>{actionable.length ? `${actionable.length} 笔待处理` : '处理已结束'}</small></div>
    {task.action === 'cancel' && pending.length > 1 && <button type="button" className="secondary" disabled={disabled || busy}
      onClick={() => void cancelAll()}>取消选中的 {pending.length} 笔申请</button>}
    {task.action === 'respond' && actionable.length > 0 && <label>付款账户<select value={payerAccount} disabled={disabled || busy}
      onChange={event => setPayerAccount(event.target.value)}>
        <option value="">请选择付款账户</option>
        {accounts.map(account => <option key={account.account_id} value={account.account_id}>
          {cardTypeLabel(account.account_type)} · 尾号 {account.card_number.slice(-4)}
        </option>)}
      </select></label>}
    <ul className="history">{task.requests.map(request => <li key={request.payment_request_id}>
      <span>{task.action === 'cancel' ? `向 ${request.payer_name} 收款` : `${request.requester_name} 向你收款`}
        {request.note && ` · ${request.note}`}<small>{request.status} · {beijingTime(request.created_at)}（北京时间）</small></span>
      <strong>¥{request.amount.toFixed(2)}</strong>
      {task.action === 'cancel' && request.status === 'PENDING' && <button type="button" className="secondary"
        disabled={disabled || busy} onClick={() => void act(request, 'cancel')}>取消这笔</button>}
      {task.action === 'respond' && ['PENDING', 'ACCEPTED'].includes(request.status) && <div className="confirm">
        <button type="button" disabled={disabled || busy || !payerAccount}
          onClick={() => void prepare(request)}>{request.status === 'ACCEPTED' ? '继续付款' : '查看付款计划'}</button>
        {request.status === 'PENDING' && <button type="button" className="secondary" disabled={disabled || busy}
          onClick={() => void act(request, 'reject')}>拒绝</button>}
      </div>}
    </li>)}</ul>
    {paymentTask && paymentRequestId && <section className="nova-transfer-review">
      <strong>付款计划 · {paymentTask.message}</strong>
      {paymentTask.confirmation_card && <p>从 {paymentTask.confirmation_card.payer_card_masked || '付款账户'}
        {' '}转给 {paymentTask.confirmation_card.recipient_name}<br />
        收款账户：{paymentTask.confirmation_card.recipient_card_masked || paymentTask.confirmation_card.recipient_phone_masked || '请核对收款账户'}<br />
        金额：¥{paymentTask.confirmation_card.amount.toFixed(2)} · 备注：{paymentTask.confirmation_card.note || '无'}</p>}
      {paymentTask.allowed_actions.includes('confirm') && <button type="button" disabled={disabled || busy}
        onClick={() => void confirm()}>确认付款</button>}
      {paymentTask.allowed_actions.includes('cancel') && <button type="button" className="secondary" disabled={disabled || busy}
        onClick={() => void cancelPaymentPlan()}>取消付款计划</button>}
      {paymentTask.allowed_actions.includes('reconcile') && <button type="button" className="secondary" disabled={disabled || busy}
        onClick={() => void reconcilePayment()}>核对银行结果</button>}
      {paymentTask.allowed_actions.includes('verify') && <div className="nova-transfer-verify">
        <SecondFactor purpose="transfer" referenceId={paymentTask.task_id} factor={factor} onChange={setFactor} disabled={disabled || busy} />
        <input aria-label="付款验证码" inputMode="numeric" maxLength={6} value={code} disabled={disabled || busy}
          onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="6 位验证码" />
        <button type="button" disabled={disabled || busy || code.length !== 6 || !factor} onClick={() => void verify()}>验证并付款</button>
      </div>}
    </section>}
    {notice && <p className="hint" role="status">{notice}</p>}
    {error && <p className="nova-error" role="alert">{error}</p>}
  </article>;
}
