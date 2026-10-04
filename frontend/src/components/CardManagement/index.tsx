import { useEffect, useState } from 'react';
import * as bank from '../../services/bank';
import { beijingTime } from '../../utils/time';
import { SecondFactor } from '../SecondFactor';

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const yuan = (fen?: number | null) => fen == null ? '-' : `¥${(fen / 100).toFixed(2)}`;
const STATUS_LABELS: Record<string, string> = { ACTIVE: '正常', LOCKED: '已锁定', LOST: '已挂失' };
const ACTION_LABELS: Record<bank.CardAction, string> = {
  card_apply_virtual: '申请虚拟卡', card_set_transaction_limits: '设置交易限额',
  card_adjust_credit_limit: '调整信用额度', card_unlock: '解锁卡片', card_report_lost: '挂失',
};
const changeValue = (change: bank.CardChange, value?: number | string | null) => {
  if (value == null) return '无';
  if (change.unit === 'fen' && typeof value === 'number') return yuan(value);
  return STATUS_LABELS[String(value)] ?? String(value);
};

type Draft = { action: bank.CardAction; card?: bank.BankCard };

export default function CardManagement({ accounts, revision, onActivity }: {
  accounts: bank.Account[]; revision: number; onActivity: () => void;
}) {
  const [cards, setCards] = useState<bank.BankCard[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [accountId, setAccountId] = useState('');
  const [singleLimit, setSingleLimit] = useState('');
  const [dailyLimit, setDailyLimit] = useState('');
  const [creditLimit, setCreditLimit] = useState('');
  const [operation, setOperation] = useState<bank.CardOperation | null>(null);
  const [code, setCode] = useState('');
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  const eligibleAccounts = accounts.filter(a => ['savings', 'checking'].includes(a.account_type));
  const errorText = (err: unknown) => {
    const detail = (err as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail?.message) return detail.message;
    return err instanceof Error ? err.message : '请求失败，请稍后重试';
  };
  const reload = async () => {
    try { setCards(await bank.listCards()); } catch (err) { setError(errorText(err)); }
  };
  useEffect(() => { reload(); }, [revision]);

  const open = (action: bank.CardAction, card?: bank.BankCard) => {
    setDraft({ action, card }); setOperation(null); setCode(''); setFactor({ method: 'totp' }); setStatus(''); setError('');
    setAccountId(eligibleAccounts[0]?.account_id ?? '');
    setSingleLimit(card ? (card.single_txn_limit_fen / 100).toFixed(2) : '');
    setDailyLimit(card ? (card.daily_txn_limit_fen / 100).toFixed(2) : '');
    setCreditLimit(card?.credit_limit_fen != null ? (card.credit_limit_fen / 100).toFixed(2) : '');
  };
  const close = () => { setDraft(null); setOperation(null); setCode(''); };

  const run = async (step: () => Promise<bank.CardOperation>) => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const result = await step();
      setOperation(result);
      if (result.status === 'COMPLETED') {
        setStatus(result.message); setDraft(null); setOperation(null); setCode('');
        await reload(); onActivity();
      } else if (['CANCELLED', 'EXPIRED', 'FAILED'].includes(result.status)) {
        setStatus(result.message); close();
      }
    } catch (err) {
      setError(errorText(err)); setCode('');
      if (operation) {
        // 授权可能已推进（过期、验证锁定、银行拒绝），以服务端为准；验证码错误时保持原步骤。
        const latest = await bank.getCardOperation(operation.operation_id).catch(() => null);
        if (!latest || latest.allowed_actions.length === 0) { setOperation(null); setDraft(null); }
        else setOperation(latest);
        await reload();
      }
    } finally { setBusy(false); }
  };

  const submitPreview = () => {
    if (!draft) return;
    const payload: bank.CardOperationRequest = { operation_id: newId(), action: draft.action };
    if (draft.action === 'card_apply_virtual') payload.account_id = accountId;
    else payload.card_id = draft.card?.card_id;
    if (draft.action === 'card_set_transaction_limits') { payload.single_txn_limit = singleLimit; payload.daily_txn_limit = dailyLimit; }
    if (draft.action === 'card_adjust_credit_limit') payload.credit_limit = creditLimit;
    run(() => bank.previewCardOperation(payload));
  };

  return <section className="card card-management">
    <div className="card-management-head"><h2>卡片管理</h2>
      <button className="secondary" disabled={busy || eligibleAccounts.length === 0} onClick={() => open('card_apply_virtual')}>申请虚拟卡</button></div>
    {status && <p className="notice">{status}</p>}
    {cards.length === 0 ? <p className="hint">暂无卡片</p> : <ul className="history">{cards.map(card => {
      const active = card.status === 'ACTIVE';
      return <li key={card.card_id}>
        <span><strong>{card.card_type === 'CREDIT' ? '信用卡' : card.is_virtual ? '虚拟借记卡' : '借记卡'} · {card.card_number}</strong>
          <small>关联账户 {card.account_label}</small>
          {card.card_type === 'CREDIT' && <small>信用额度 {yuan(card.credit_limit_fen)}（银行核定上限 {yuan(card.credit_limit_cap_fen)}）</small>}
          <small>单笔限额 {yuan(card.single_txn_limit_fen)} · 单日限额 {yuan(card.daily_txn_limit_fen)}</small>
          {card.lock_reason && <small>锁定原因：{card.lock_reason}</small>}
          {card.lost_reported_at && <small>挂失时间（北京时间）：{beijingTime(card.lost_reported_at)}</small>}</span>
        <div className="subscription-action"><strong className={active ? 'in' : card.status === 'LOST' ? 'out' : 'hint'}>{STATUS_LABELS[card.status]}</strong>
          {card.status !== 'LOST' && <div className="card-actions">
            {active && <button className="secondary" disabled={busy} onClick={() => open('card_set_transaction_limits', card)}>交易限额</button>}
            {active && card.card_type === 'CREDIT' && <button className="secondary" disabled={busy} onClick={() => open('card_adjust_credit_limit', card)}>调整额度</button>}
            {card.status === 'LOCKED' && <button className="secondary" disabled={busy} onClick={() => open('card_unlock', card)}>解锁</button>}
            <button className="secondary" disabled={busy} onClick={() => open('card_report_lost', card)}>挂失</button>
          </div>}</div>
      </li>;
    })}</ul>}

    {draft && !operation && <div className="subscription-confirm">
      <p><strong>{ACTION_LABELS[draft.action]}{draft.card && ` · ${draft.card.card_number}`}</strong></p>
      {draft.action === 'card_apply_virtual' && <label>关联借记账户<select value={accountId} onChange={e => setAccountId(e.target.value)}>{eligibleAccounts.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>}
      {draft.action === 'card_set_transaction_limits' && draft.card && <>
        <label>单笔限额（元，银行上限 {yuan(draft.card.single_txn_cap_fen)}）<input type="number" min="0.01" step="0.01" value={singleLimit} onChange={e => setSingleLimit(e.target.value)} /></label>
        <label>单日限额（元，银行上限 {yuan(draft.card.daily_txn_cap_fen)}）<input type="number" min="0.01" step="0.01" value={dailyLimit} onChange={e => setDailyLimit(e.target.value)} /></label></>}
      {draft.action === 'card_adjust_credit_limit' && draft.card && <label>新信用额度（元，银行核定上限 {yuan(draft.card.credit_limit_cap_fen)}）<input type="number" min="0.01" step="0.01" value={creditLimit} onChange={e => setCreditLimit(e.target.value)} /></label>}
      {draft.action === 'card_report_lost' && <p className="hint">挂失后该卡永久停用，不能解锁恢复。</p>}
      <p className="hint">提交后由银行校验并返回变更明细，尚不会执行。</p>
      <div className="confirm"><button className="secondary" disabled={busy} onClick={close}>返回</button>
        <button disabled={busy || (draft.action === 'card_apply_virtual' && !accountId)} onClick={submitPreview}>{busy ? '正在校验…' : '生成确认信息'}</button></div>
    </div>}

    {operation && <div className="subscription-confirm">
      <p><strong>{ACTION_LABELS[operation.action]}</strong> <span className={`card-level card-level-${operation.permission_level}`}>{operation.permission_level === 'red' ? '🔴 需确认 + 验证器/短信' : '🟡 需本人确认'}</span></p>
      <p>{operation.message}</p>
      <ul className="history">{operation.changes.map(change => <li key={change.field}><span>{change.label}</span><span>{changeValue(change, change.before)} → <strong>{changeValue(change, change.after)}</strong></span></li>)}</ul>
      <small>以上数值均由银行返回；确认有效期至（北京时间）{beijingTime(operation.expires_at)}</small>
      {operation.allowed_actions.includes('verify') && <SecondFactor purpose="card" referenceId={operation.operation_id} factor={factor} onChange={setFactor} disabled={busy} />}
      {operation.allowed_actions.includes('verify') && <div className="row"><input inputMode="numeric" maxLength={6} value={code} placeholder="6 位验证码" onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} onKeyDown={e => e.key === 'Enter' && code.length === 6 && factor && run(() => bank.verifyCardOperation(operation, code, factor))} /></div>}
      <div className="confirm">
        {operation.allowed_actions.includes('cancel') && <button className="secondary" disabled={busy} onClick={() => run(() => bank.cancelCardOperation(operation))}>取消</button>}
        {operation.allowed_actions.includes('confirm') && <button disabled={busy} onClick={() => run(() => bank.confirmCardOperation(operation))}>{busy ? '处理中…' : operation.requires_mfa ? '确认，下一步验证' : '确认执行'}</button>}
        {operation.allowed_actions.includes('verify') && <button disabled={busy || !factor || code.length !== 6} onClick={() => factor && run(() => bank.verifyCardOperation(operation, code, factor))}>{busy ? '验证中…' : '提交验证'}</button>}
      </div>
    </div>}
    {error && <p className="error">{error}</p>}
  </section>;
}
