import { useEffect, useState } from 'react';
import * as bank from '../services/bank';
import { dismissNovaSubscriptionTask, selectNovaSubscriptionTask, type NovaChatSubscriptionTask } from '../services/nova';
import { beijingTime } from '../utils/time';

const CYCLES: Record<string, string> = { MONTHLY: '每月', YEARLY: '每年', WEEKLY: '每周' };
const amountText = (subscription: bank.Subscription) =>
  `${(subscription.amount_fen / 100).toFixed(2)} ${subscription.currency} / ${CYCLES[subscription.billing_cycle] ?? subscription.billing_cycle}`;
const errorText = (err: unknown) => {
  const detail = (err as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return err instanceof Error ? err.message : '请求失败，请稍后重试';
};

/** NOVA 发起的取消订阅：本人在聊天中查看银行返回的取消详情并确认；确认凭证不经过 NOVA。 */
export default function NovaSubscriptionCancelCard({ task, sessionId, disabled, onUpdated, onActivity }: {
  task: NovaChatSubscriptionTask; sessionId: string; disabled: boolean;
  onUpdated: (tasks: NovaChatSubscriptionTask[]) => void; onActivity?: () => void;
}) {
  const [subscription, setSubscription] = useState<bank.Subscription | null>(task.subscription ?? null);
  const [choices, setChoices] = useState<bank.Subscription[]>([]);
  const [choice, setChoice] = useState('');
  const [preview, setPreview] = useState<bank.SubscriptionCancellationPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { setSubscription(task.subscription ?? null); }, [task.subscription?.subscription_id, task.subscription?.status]);
  // 未指定订阅时列出本人生效中的订阅供选择。
  useEffect(() => {
    if (task.subscription_id) return;
    let live = true;
    bank.listSubscriptions().then(items => { if (live) setChoices(items.filter(item => item.status === 'ACTIVE')); })
      .catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [task.entry_id, task.subscription_id]);

  const act = async (step: () => Promise<void>) => {
    if (busy || disabled) return;
    setBusy(true); setError('');
    try { await step(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const select = () => act(async () => { onUpdated(await selectNovaSubscriptionTask(sessionId, task.entry_id, choice)); });
  const dismiss = () => act(async () => { onUpdated(await dismissNovaSubscriptionTask(sessionId, task.entry_id)); });
  const review = () => act(async () => {
    const result = await bank.previewSubscriptionCancellation(subscription!);
    setPreview(result); setSubscription(result.subscription);
  });
  const confirm = () => act(async () => {
    try {
      const updated = await bank.cancelSubscription(preview!.subscription, preview!.confirmation_token);
      setSubscription(updated); setPreview(null); onActivity?.();
    } catch (err) { setPreview(null); throw err; }
  });

  const off = busy || disabled;
  const cancelled = subscription?.status === 'CANCELLED';
  return <article className="card nova-transfer-card nova-subscription-cancel" aria-label="取消订阅任务">
    <div className="nova-transfer-card-head"><div>
      <strong>取消订阅{subscription ? ` · ${subscription.merchant_name}` : ''}</strong>
      <small>{cancelled ? '已取消 · 不再计划模拟扣费' : '尚未取消 · 需本人确认'}</small>
    </div></div>

    {!subscription && <form className="nova-collection-form" onSubmit={event => { event.preventDefault(); void select(); }}>
      <label>选择要取消的订阅<select value={choice} disabled={off} onChange={e => setChoice(e.target.value)}>
        <option value="">{choices.length ? '请选择订阅' : '没有生效中的订阅'}</option>
        {choices.map(item => <option key={item.subscription_id} value={item.subscription_id}>{item.merchant_name} · {amountText(item)}</option>)}
      </select></label>
      <div className="nova-transfer-actions">
        <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>不办理</button>
        <button type="submit" disabled={off || !choice}>下一步</button>
      </div>
    </form>}

    {subscription && <>
      <ul className="history">
        <li><span>商户</span><span>{subscription.merchant_name}</span></li>
        <li><span>周期金额</span><span>{amountText(subscription)}</span></li>
        <li><span>扣费账户</span><span>{subscription.debit_account_label}</span></li>
        {cancelled ? <li><span>取消时间（北京时间）</span><span>{subscription.cancelled_at ? beijingTime(subscription.cancelled_at) : '-'}</span></li>
          : <li><span>计划下次扣费（北京时间）</span><span>{beijingTime(subscription.next_charge_at)}</span></li>}
      </ul>
      {preview && <div className="nova-transfer-review">
        <p>{preview.message}</p>
        <small className="hint">以上信息由银行返回；确认有效期至（北京时间）{beijingTime(preview.expires_at)}。NOVA 不能代为确认。</small>
      </div>}
      {!cancelled && <div className="nova-transfer-actions">
        {!preview && <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>不取消</button>}
        {preview && <button type="button" className="secondary" disabled={off} onClick={() => setPreview(null)}>返回</button>}
        {!preview ? <button type="button" disabled={off} onClick={() => void review()}>{busy ? '正在读取…' : '查看取消详情'}</button>
          : <button type="button" disabled={off} onClick={() => void confirm()}>{busy ? '正在取消…' : '确认取消订阅'}</button>}
      </div>}
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </article>;
}
