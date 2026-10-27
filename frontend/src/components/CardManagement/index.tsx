import { useEffect, useRef, useState } from 'react';
import * as bank from '../../services/bank';
import { beijingTime } from '../../utils/time';
import { SecondFactor } from '../SecondFactor';
import { formatFen } from '../../utils/money';

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const yuan = (fen?: number | null) => fen == null ? '-' : formatFen(fen);
const STATUS_LABELS: Record<string, string> = { ACTIVE: '正常', LOCKED: '已锁定', LOST: '已挂失', CANCELLED: '已注销' };
const ACTION_LABELS: Record<bank.CardAction, string> = {
  card_apply_virtual: '申请虚拟卡', card_set_transaction_limits: '设置交易限额',
  card_adjust_credit_limit: '调整信用额度', card_unlock: '解锁卡片', card_report_lost: '挂失',
  card_lock: '锁定卡片', card_cancel_virtual: '注销虚拟卡', card_reissue: '挂失补卡', card_set_channels: '设置交易渠道',
};
const CHANNELS: [bank.CardChannelSwitch, string][] = [
  ['online_enabled', '线上支付'], ['atm_enabled', 'ATM取现'], ['overseas_enabled', '境外交易'], ['contactless_enabled', '免密/闪付'],
];
const PURCHASE_CHANNELS: [bank.CardTransactionChannel, string][] = [
  ['pos', '线下刷卡'], ['online', '线上支付'], ['contactless', '免密/闪付'], ['atm', 'ATM取现'], ['overseas', '境外交易'],
];
const ACTION_HINTS: Partial<Record<bank.CardAction, string>> = {
  card_report_lost: '挂失后该卡永久停用，不能解锁恢复；可随后申请补卡。如只是暂时找不到卡，可先锁卡。',
  card_cancel_virtual: '注销后该虚拟卡永久停用，不能恢复。',
  card_lock: '锁定后该卡所有交易都会被拒绝，解锁需要确认并通过验证码。',
  card_reissue: '新卡沿用原卡的限额、渠道设置和信用额度，银行签发新卡号。',
};
const changeValue = (change: bank.CardChange, value?: boolean | number | string | null) => {
  if (value == null) return '无';
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  if (change.unit === 'fen' && typeof value === 'number') return yuan(value);
  return STATUS_LABELS[String(value)] ?? String(value);
};
const cardKind = (card: bank.BankCard) => card.card_type === 'CREDIT' ? '信用卡' : card.is_virtual ? '虚拟借记卡' : '借记卡';
const closed = (card: bank.BankCard) => card.status === 'LOST' || card.status === 'CANCELLED';

type Draft = { action: bank.CardAction; card?: bank.BankCard };
type Channels = Record<bank.CardChannelSwitch, boolean>;

export default function CardManagement({ accounts, revision, onActivity, selectedCardId, onSelectCard, pendingOperationId, onPendingHandled }: {
  accounts: bank.Account[]; revision: number; onActivity: () => void;
  selectedCardId?: string; onSelectCard: (id: string | undefined) => void;
  pendingOperationId?: string; onPendingHandled?: () => void;
}) {
  const [cards, setCards] = useState<bank.BankCard[]>([]);
  const [showClosed, setShowClosed] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [accountId, setAccountId] = useState('');
  const [singleLimit, setSingleLimit] = useState('');
  const [dailyLimit, setDailyLimit] = useState('');
  const [creditLimit, setCreditLimit] = useState('');
  const [channels, setChannels] = useState<Channels>({ online_enabled: true, atm_enabled: true, overseas_enabled: false, contactless_enabled: true });
  const [operation, setOperation] = useState<bank.CardOperation | null>(null);
  const [code, setCode] = useState('');
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [purchaseCard, setPurchaseCard] = useState<bank.BankCard | null>(null);
  const [purchase, setPurchase] = useState({ amount: '', merchant: '', channel: 'pos' as bank.CardTransactionChannel });
  const [historyCard, setHistoryCard] = useState<bank.BankCard | null>(null);
  const [history, setHistory] = useState<bank.CardAuthorization[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const panel = useRef<HTMLDivElement>(null);

  const eligibleAccounts = accounts.filter(a => ['savings', 'checking'].includes(a.account_type));
  const visibleCards = showClosed ? cards : cards.filter(card => !closed(card));
  const closedCount = cards.length - cards.filter(card => !closed(card)).length;
  const errorText = (err: unknown) => {
    const detail = (err as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail?.message) return detail.message;
    return err instanceof Error ? err.message : '请求失败，请稍后重试';
  };
  const reload = async () => {
    try { const items = await bank.listCards(); setCards(items); if (selectedCardId && !items.some(card => card.card_id === selectedCardId)) onSelectCard(undefined); return items; } catch (err) { setError(errorText(err)); return null; }
  };
  const loadHistory = async (card: bank.BankCard) => {
    try { setHistory(await bank.listCardTransactions(card.card_id)); } catch (err) { setError(errorText(err)); }
  };
  useEffect(() => { reload(); }, [revision]);

  // NOVA 只生成预览；在此处由本人核对、确认并完成验证。
  useEffect(() => {
    if (!pendingOperationId) return;
    let cancelled = false;
    (async () => {
      try {
        const [latest, items] = await Promise.all([bank.getCardOperation(pendingOperationId), reload()]);
        if (cancelled) return;
        setDraft({ action: latest.action, card: items?.find(card => card.card_id === latest.card_id) });
        setPurchaseCard(null); setCode(''); setFactor({ method: 'totp' }); setError('');
        if (latest.allowed_actions.length === 0) { setOperation(null); setDraft(null); setStatus(latest.message); }
        else { setOperation(latest); setStatus('NOVA 已生成以下变更，尚未执行；请核对后由你本人确认。'); }
        setTimeout(() => panel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 0);
      } catch (err) { if (!cancelled) setError(errorText(err)); }
      finally { if (!cancelled) onPendingHandled?.(); }
    })();
    return () => { cancelled = true; };
  }, [pendingOperationId]);

  const open = (action: bank.CardAction, card?: bank.BankCard) => {
    setDraft({ action, card }); setOperation(null); setPurchaseCard(null); setCode(''); setFactor({ method: 'totp' }); setStatus(''); setError('');
    setAccountId(eligibleAccounts[0]?.account_id ?? '');
    setSingleLimit(card ? (card.single_txn_limit_fen / 100).toFixed(2) : '');
    setDailyLimit(card ? (card.daily_txn_limit_fen / 100).toFixed(2) : '');
    setCreditLimit(card?.credit_limit_fen != null ? (card.credit_limit_fen / 100).toFixed(2) : '');
    if (card) setChannels({ online_enabled: card.online_enabled, atm_enabled: card.atm_enabled, overseas_enabled: card.overseas_enabled, contactless_enabled: card.contactless_enabled });
  };
  const close = () => { setDraft(null); setOperation(null); setCode(''); };
  const openPurchase = (card: bank.BankCard) => {
    close(); setStatus(''); setError(''); setPurchaseCard(card); setPurchase({ amount: '', merchant: '', channel: 'pos' });
  };
  const toggleHistory = (card: bank.BankCard) => {
    if (historyCard?.card_id === card.card_id) { setHistoryCard(null); return; }
    setHistoryCard(card); setHistory([]); loadHistory(card);
  };

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
    if (draft.action === 'card_set_channels') Object.assign(payload, channels);
    run(() => bank.previewCardOperation(payload));
  };

  const submitPurchase = async () => {
    if (!purchaseCard || busy) return;
    setBusy(true); setError(''); setStatus('');
    try {
      const { authorization } = await bank.simulateCardPurchase(purchaseCard.card_id, { operation_id: newId(), ...purchase, merchant: purchase.merchant.trim() });
      setStatus(`${authorization.approved ? '✅' : '⛔'} ${purchase.merchant.trim()} ${yuan(authorization.amount_fen)}：${authorization.message}`);
      await reload();
      if (historyCard?.card_id === purchaseCard.card_id) await loadHistory(purchaseCard);
      if (authorization.approved) onActivity();
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };

  return <section className="card card-management">
    <div className="card-management-head"><h2>卡片管理</h2>
      <button className="secondary" disabled={busy || eligibleAccounts.length === 0} onClick={() => open('card_apply_virtual')}>申请虚拟卡</button></div>
    <p className="hint">选择一张卡片后，可向 NOVA 询问“这张卡的限额是多少？”，或让 NOVA 生成锁卡、调整限额等变更，并在对话中的卡片任务卡里由你本人确认。</p>
    {closedCount > 0 && <label className="hint"><input type="checkbox" checked={showClosed} onChange={e => setShowClosed(e.target.checked)} /> 显示已挂失/已注销卡片（{closedCount}）</label>}
    {status && <p className="notice">{status}</p>}
    {visibleCards.length === 0 ? <p className="hint">暂无卡片</p> : <ul className="history card-list">{visibleCards.map(card => {
      const active = card.status === 'ACTIVE';
      return <li key={card.card_id} className={selectedCardId === card.card_id ? 'nova-selected-row' : undefined}>
        <span><strong>{cardKind(card)} · {card.card_number}</strong>
          <small>关联账户 {card.account_label}{card.valid_thru && ` · 有效期至 ${card.valid_thru}`}</small>
          {card.card_type === 'CREDIT' && <small>信用额度 {yuan(card.credit_limit_fen)} · 已用 {yuan(card.used_credit_fen)} · 可用 {yuan(card.available_credit_fen)}（银行核定上限 {yuan(card.credit_limit_cap_fen)}）</small>}
          <small>单笔限额 {yuan(card.single_txn_limit_fen)} · 单日限额 {yuan(card.daily_txn_limit_fen)}</small>
          {!closed(card) && <small>渠道：{CHANNELS.map(([key, label]) => `${label}${card[key] ? '开' : '关'}`).join(' · ')}</small>}
          {card.lock_reason && <small>锁定原因：{card.lock_reason}</small>}
          {card.lost_reported_at && <small>挂失时间（北京时间）：{beijingTime(card.lost_reported_at)}</small>}
          {card.replaced_by_card_id && <small>已补发新卡 {cards.find(c => c.card_id === card.replaced_by_card_id)?.card_number ?? '，卡片信息待刷新'}</small>}
          {card.cancelled_at && <small>注销时间（北京时间）：{beijingTime(card.cancelled_at)}</small>}</span>
        <div className="subscription-action"><button className="secondary" aria-pressed={selectedCardId === card.card_id} onClick={() => onSelectCard(selectedCardId === card.card_id ? undefined : card.card_id)}>{selectedCardId === card.card_id ? '已选中 · 再点取消' : '选择供 NOVA 查询'}</button><strong data-card-status={card.status} className={active ? 'in' : closed(card) ? 'out' : 'hint'}>{STATUS_LABELS[card.status]}</strong>
          <div className="card-actions">
            {active && <button className="secondary" disabled={busy} onClick={() => open('card_set_transaction_limits', card)}>交易限额</button>}
            {active && card.card_type === 'CREDIT' && <button className="secondary" disabled={busy} onClick={() => open('card_adjust_credit_limit', card)}>调整额度</button>}
            {active && <button className="secondary" disabled={busy} onClick={() => open('card_set_channels', card)}>交易渠道</button>}
            {active && <button className="secondary" disabled={busy} onClick={() => open('card_lock', card)}>锁卡</button>}
            {card.status === 'LOCKED' && <button className="secondary" disabled={busy} onClick={() => open('card_unlock', card)}>解锁</button>}
            {!closed(card) && card.is_virtual && <button className="secondary card-danger" disabled={busy} onClick={() => open('card_cancel_virtual', card)}>注销</button>}
            {!closed(card) && <button className="secondary card-danger" disabled={busy} onClick={() => open('card_report_lost', card)}>挂失</button>}
            {card.status === 'LOST' && !card.replaced_by_card_id && <button className="secondary" disabled={busy} onClick={() => open('card_reissue', card)}>补卡</button>}
            {card.status !== 'CANCELLED' && card.status !== 'LOST' && <button className="secondary card-purchase" disabled={busy} onClick={() => openPurchase(card)}>模拟消费</button>}
            <button className="secondary card-history" disabled={busy} aria-pressed={historyCard?.card_id === card.card_id} onClick={() => toggleHistory(card)}>交易记录</button>
          </div></div>
      </li>;
    })}</ul>}

    <div ref={panel}>
    {draft && !operation && <div className="subscription-confirm">
      <p><strong>{ACTION_LABELS[draft.action]}{draft.card && ` · ${draft.card.card_number}`}</strong></p>
      {draft.action === 'card_apply_virtual' && <label>关联借记账户<select value={accountId} onChange={e => setAccountId(e.target.value)}>{eligibleAccounts.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>}
      {draft.action === 'card_set_transaction_limits' && draft.card && <>
        <label>单笔限额（元，银行上限 {yuan(draft.card.single_txn_cap_fen)}）<input type="number" min="0.01" step="0.01" value={singleLimit} onChange={e => setSingleLimit(e.target.value)} /></label>
        <label>单日限额（元，银行上限 {yuan(draft.card.daily_txn_cap_fen)}）<input type="number" min="0.01" step="0.01" value={dailyLimit} onChange={e => setDailyLimit(e.target.value)} /></label>
        <p className="hint">只调低限额需本人确认；调高任一项还需验证码。</p></>}
      {draft.action === 'card_adjust_credit_limit' && draft.card && <>
        <label>新信用额度（元，银行核定上限 {yuan(draft.card.credit_limit_cap_fen)}，不低于已用 {yuan(draft.card.used_credit_fen)}）<input type="number" min="0.01" step="0.01" value={creditLimit} onChange={e => setCreditLimit(e.target.value)} /></label>
        <p className="hint">降额需本人确认；提额还需验证码。</p></>}
      {draft.action === 'card_set_channels' && <>
        {CHANNELS.map(([key, label]) => <label key={key}><input type="checkbox" checked={channels[key]} onChange={e => setChannels({ ...channels, [key]: e.target.checked })} /> {label}</label>)}
        <p className="hint">只关闭渠道需本人确认；打开任一渠道还需验证码。</p></>}
      {ACTION_HINTS[draft.action] && <p className="hint">{ACTION_HINTS[draft.action]}</p>}
      <p className="hint">提交后由银行校验并返回变更明细，尚不会执行。</p>
      <div className="confirm"><button className="secondary" disabled={busy} onClick={close}>返回</button>
        <button disabled={busy || (draft.action === 'card_apply_virtual' && !accountId)} onClick={submitPreview}>{busy ? '正在校验…' : '生成确认信息'}</button></div>
    </div>}

    {operation && <div className="subscription-confirm">
      <p><strong>{ACTION_LABELS[operation.action]}{draft?.card && ` · ${draft.card.card_number}`}</strong> <span className={`card-level card-level-${operation.permission_level}`}>{operation.permission_level === 'red' ? '🔴 需确认 + 验证器/短信' : '🟡 需本人确认'}</span></p>
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

    {purchaseCard && <div className="subscription-confirm">
      <p><strong>模拟消费 · {purchaseCard.card_number}</strong></p>
      <p className="hint">模拟商户发起的用卡交易：银行按卡片状态、有效期、渠道开关、单笔/单日限额及可用余额或信用额度实时决定批准或拒绝。</p>
      <label>商户<input maxLength={64} value={purchase.merchant} placeholder="例如：便利店" onChange={e => setPurchase({ ...purchase, merchant: e.target.value })} /></label>
      <label>金额（元）<input type="number" min="0.01" step="0.01" value={purchase.amount} onChange={e => setPurchase({ ...purchase, amount: e.target.value })} /></label>
      <label>渠道<select value={purchase.channel} onChange={e => setPurchase({ ...purchase, channel: e.target.value as bank.CardTransactionChannel })}>{PURCHASE_CHANNELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <div className="confirm"><button className="secondary" disabled={busy} onClick={() => setPurchaseCard(null)}>关闭</button>
        <button disabled={busy || !purchase.amount || !purchase.merchant.trim()} onClick={submitPurchase}>{busy ? '银行授权中…' : '发起交易'}</button></div>
    </div>}
    </div>

    {historyCard && <div className="subscription-confirm card-transaction-history">
      <p><strong>交易记录 · {historyCard.card_number}</strong>（最近20笔，含被拒绝的交易）</p>
      {history.length === 0 ? <p className="hint">暂无用卡交易</p> : <ul className="history">{history.map(row => <li key={row.auth_id}>
        <span><strong>{row.merchant}</strong><small>{beijingTime(row.authorized_at)} · {PURCHASE_CHANNELS.find(([value]) => value === row.channel)?.[1] ?? row.channel}</small></span>
        <span className={row.approved ? 'out' : 'hint'}>{yuan(row.amount_fen)} · {row.approved ? '已批准' : row.message}</span>
      </li>)}</ul>}
    </div>}
    {error && <p className="error">{error}</p>}
  </section>;
}
