import { useEffect, useState } from 'react';
import { decideNovaBillAction, getNovaBillActions, type NovaBillAction } from '../services/nova';
import './novaBills.css';

export default function NovaBillCard({ card, disabled, onUpdated, onActivity }: {
  card: NovaBillAction; disabled: boolean; onUpdated: (card: NovaBillAction) => void; onActivity?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (card.status !== 'pending') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [card.status]);
  const status = card.status === 'pending' && now >= Date.parse(card.expires_at) ? 'expired' : card.status;
  const act = async (decision: 'confirm' | 'cancel') => {
    if (busy || disabled) return;
    setBusy(true); setError('');
    try {
      const updated = await decideNovaBillAction(card, decision);
      onUpdated(updated);
      if (updated.status === 'completed') onActivity?.();
    } catch (failure) {
      const message = (failure as {response?: {data?: {detail?: {message?: string}}}})?.response?.data?.detail?.message;
      setError(message || '结果尚未确认，请重试或重新读取原卡片。');
      // A lost response may have committed. Recover the same receipt rather
      // than creating a replacement action or claiming nothing happened.
      try {
        const fresh = (await getNovaBillActions(card.session_id)).find(item => item.action_id === card.action_id);
        if (fresh) { onUpdated(fresh); if (fresh.status === 'completed') { setError(''); onActivity?.(); } }
      } catch { /* Keep the visible uncertainty and retry the original card. */ }
    } finally { setBusy(false); }
  };
  return <article className="card nova-transfer-card nova-bill-card" aria-label="账单修改确认卡">
    <div className="nova-transfer-card-head"><div><strong>{card.title}</strong><small>{({pending:'待本人确认 · 尚未保存',completed:'已确认保存',cancelled:'已取消 · 未保存',expired:'已过期 · 请重新生成'})[status]}</small></div></div>
    {card.fields?.length?<><dl className="nova-bill-fields">{card.fields.map((field,index)=><div key={index}><dt>{field.label}</dt><dd>{field.value!==undefined?field.value:<span className="nova-bill-diff"><span><small>修改前</small>{field.before}</span><span aria-hidden="true">→</span><span><small>修改后</small><strong>{field.after}</strong></span></span>}</dd></div>)}</dl><details className="nova-transfer-review"><summary>查看完整影响说明</summary>{card.lines.map((line,index)=><p key={index}>{line}</p>)}</details></>:<dl className="nova-bill-fields">{card.lines.map((line,index)=><div key={index}><dt>变更说明</dt><dd>{line}</dd></div>)}</dl>}
    <p className="hint">{card.notice}</p>
    {status === 'pending' && <><small>有效至 {new Date(card.expires_at).toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false})}（北京时间）</small><div className="nova-transfer-actions">
      <button type="button" className="secondary" disabled={disabled || busy} onClick={() => void act('cancel')}>取消这项修改</button>
      <button type="button" disabled={disabled || busy} onClick={() => void act('confirm')}>{busy ? '正在核对…' : '确认保存'}</button>
    </div></>}
    {error && <p className="error" role="alert">{error}</p>}
  </article>;
}
