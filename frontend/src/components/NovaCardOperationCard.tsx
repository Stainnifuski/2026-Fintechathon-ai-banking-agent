import {useTaskField,useTaskPrefill} from './NovaTaskPresentation';
import { useEffect, useState } from 'react';
import * as bank from '../services/bank';
import { dismissNovaCardTask, linkNovaCardTask, type NovaChatCardTask } from '../services/nova';
import { beijingTime } from '../utils/time';
import { SecondFactor } from './SecondFactor';
import { formatFen } from '../utils/money';

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const ACTION_LABELS: Record<bank.CardAction, string> = {
  card_apply_virtual: '申请虚拟卡', card_set_transaction_limits: '设置交易限额',
  card_adjust_credit_limit: '调整信用额度', card_unlock: '解锁卡片', card_report_lost: '挂失',
  card_lock: '锁定卡片', card_cancel_virtual: '注销虚拟卡', card_reissue: '挂失补卡', card_set_channels: '设置交易渠道',
};
const ACTION_HINTS: Partial<Record<bank.CardAction, string>> = {
  card_report_lost: '挂失后该卡永久停用，不能解锁恢复；可随后申请补卡。如只是暂时找不到卡，可先锁卡。',
  card_cancel_virtual: '注销后该虚拟卡永久停用，不能恢复。',
  card_lock: '锁定后该卡所有交易都会被拒绝，解锁需要确认并通过验证码。',
  card_reissue: '新卡沿用原卡的限额、渠道设置和信用额度，银行签发新卡号。',
};
const CHANNELS: [bank.CardChannelSwitch, string][] = [
  ['online_enabled', '线上支付'], ['atm_enabled', 'ATM取现'], ['overseas_enabled', '境外交易'], ['contactless_enabled', '免密/闪付'],
];
const STATUS_LABELS: Record<string, string> = { ACTIVE: '正常', LOCKED: '已锁定', LOST: '已挂失', CANCELLED: '已注销' };
// 与卡片页按钮出现条件一致：只列出当前状态允许该操作的卡。
const ELIGIBLE: Record<bank.CardAction, (card: bank.BankCard) => boolean> = {
  card_apply_virtual: () => false,
  card_set_transaction_limits: card => card.status === 'ACTIVE',
  card_adjust_credit_limit: card => card.status === 'ACTIVE' && card.card_type === 'CREDIT',
  card_set_channels: card => card.status === 'ACTIVE',
  card_lock: card => card.status === 'ACTIVE',
  card_unlock: card => card.status === 'LOCKED',
  card_report_lost: card => card.status === 'ACTIVE' || card.status === 'LOCKED',
  card_cancel_virtual: card => card.is_virtual && (card.status === 'ACTIVE' || card.status === 'LOCKED'),
  card_reissue: card => card.status === 'LOST' && !card.replaced_by_card_id,
};
const EDITABLE = new Set<bank.CardAction>(['card_apply_virtual', 'card_set_transaction_limits', 'card_adjust_credit_limit', 'card_set_channels']);
const yuan = (fen?: number | null) => fen == null ? '-' : formatFen(fen);
const fenText = (fen?: number | null) => fen == null ? '' : (fen / 100).toFixed(2);
const changeValue = (change: bank.CardChange, value?: boolean | number | string | null) => {
  if (value == null) return '无';
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  if (change.unit === 'fen' && typeof value === 'number') return yuan(value);
  return STATUS_LABELS[String(value)] ?? String(value);
};
const errorText = (err: unknown) => {
  const detail = (err as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return err instanceof Error ? err.message : '请求失败，请稍后重试';
};
type Channels = Record<bank.CardChannelSwitch, boolean>;

/** NOVA 识别出的卡片操作在聊天中以表单办理：本人填写/修改 → 生成确认信息 → 本人确认（及验证）。 */
export default function NovaCardOperationCard({ task, sessionId, accounts, disabled, onUpdated, onActivity }: {
  task: NovaChatCardTask; sessionId: string; accounts: bank.Account[]; disabled: boolean;
  onUpdated: (tasks: NovaChatCardTask[]) => void; onActivity?: () => void;
}) {
  const prefill = task.prefill;
  const [operation, setOperation] = useState<bank.CardOperation | null>(task.operation ?? null);
  const [editing, setEditing] = useState(!task.operation);
  const [cards, setCards] = useState<bank.BankCard[]>(task.card ? [task.card] : []);
  const [cardId, setCardId] = useTaskField('cardId',task.card_id ?? '');
  const eligibleAccounts = accounts.filter(a => ['savings', 'checking'].includes(a.account_type));
  const [accountId, setAccountId] = useTaskField('accountId',String(prefill.account_id ?? ''));
  const [singleLimit, setSingleLimit] = useTaskField('singleLimit',String(prefill.single_txn_limit ?? fenText(task.card?.single_txn_limit_fen)));
  const [dailyLimit, setDailyLimit] = useTaskField('dailyLimit',String(prefill.daily_txn_limit ?? fenText(task.card?.daily_txn_limit_fen)));
  const [creditLimit, setCreditLimit] = useTaskField('creditLimit',String(prefill.credit_limit ?? ''));
  const [channels, setChannels] = useTaskField<Channels>('channels',() => Object.fromEntries(CHANNELS.map(([key]) =>
    [key, typeof prefill[key] === 'boolean' ? prefill[key] as boolean : task.card?.[key] ?? false])) as Channels);
  const [code, setCode] = useState('');
  useEffect(()=>{const clear=()=>setCode('');window.addEventListener('nova-clear-verification',clear);return()=>window.removeEventListener('nova-clear-verification',clear);},[]);
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(task.notice ?? '');
  useTaskPrefill({...prefill,card_id:task.card_id},{card_id:v=>setCardId(String(v??'')),account_id:v=>setAccountId(String(v??'')),single_txn_limit:v=>setSingleLimit(String(v??'')),daily_txn_limit:v=>setDailyLimit(String(v??'')),credit_limit:v=>setCreditLimit(String(v??'')),...Object.fromEntries(CHANNELS.map(([key])=>[key,(v:unknown)=>{if(typeof v==='boolean')setChannels(old=>({...old,[key]:v}));}]))});

  useEffect(() => { setOperation(task.operation ?? null); if (task.operation) setEditing(false); },
    [task.operation?.operation_id, task.operation?.status]);
  // 未指定卡片时，在表单中列出本人当前允许该操作的卡片供选择。
  useEffect(() => {
    if (task.card_id || task.action === 'card_apply_virtual') return;
    let live = true;
    bank.listCards().then(items => { if (live) setCards(items.filter(ELIGIBLE[task.action])); })
      .catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [task.entry_id]);
  useEffect(() => { if (!accountId && eligibleAccounts[0]) setAccountId(eligibleAccounts[0].account_id); }, [accounts]);

  const card = cards.find(item => item.card_id === cardId) ?? task.card ?? operation?.card ?? null;
  const chooseCard = (id: string) => {
    setCardId(id);
    const next = cards.find(item => item.card_id === id);
    if (!next) return;
    if (!prefill.single_txn_limit) setSingleLimit(fenText(next.single_txn_limit_fen));
    if (!prefill.daily_txn_limit) setDailyLimit(fenText(next.daily_txn_limit_fen));
    setChannels(Object.fromEntries(CHANNELS.map(([key]) =>
      [key, typeof prefill[key] === 'boolean' ? prefill[key] as boolean : next[key]])) as Channels);
  };

  const run = async (step: () => Promise<bank.CardOperation>) => {
    if (busy || disabled) return;
    setBusy(true); setError('');
    try {
      const result = await step();
      setOperation(result); setCode('');
      if (result.status === 'COMPLETED') onActivity?.();
    } catch (err) {
      setError(errorText(err)); setCode('');
      // 授权可能已推进（过期、验证锁定、银行拒绝），以服务端为准。
      if (operation) { const latest = await bank.getCardOperation(operation.operation_id).catch(() => null); if (latest) setOperation(latest); }
    } finally { setBusy(false); }
  };

  const submitForm = async () => {
    if (busy || disabled) return;
    const payload: bank.CardOperationRequest = { operation_id: newId(), action: task.action };
    if (task.action === 'card_apply_virtual') payload.account_id = accountId;
    else payload.card_id = cardId;
    if (task.action === 'card_set_transaction_limits') { payload.single_txn_limit = singleLimit; payload.daily_txn_limit = dailyLimit; }
    if (task.action === 'card_adjust_credit_limit') payload.credit_limit = creditLimit;
    if (task.action === 'card_set_channels') Object.assign(payload, channels);
    setBusy(true); setError('');
    try {
      const preview = await bank.previewCardOperation(payload);
      const tasks = await linkNovaCardTask(sessionId, task.entry_id, preview.operation_id);
      setOperation(preview); setEditing(false); onUpdated(tasks);
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };

  const dismiss = async () => {
    if (busy || disabled) return;
    setBusy(true); setError('');
    try { onUpdated(await dismissNovaCardTask(sessionId, task.entry_id)); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };

  const allowed = operation?.allowed_actions ?? [];
  const open = allowed.length > 0;
  const off = busy || disabled;
  const ready = task.action === 'card_apply_virtual' ? !!accountId : !!cardId && (
    task.action === 'card_set_transaction_limits' ? !!singleLimit && !!dailyLimit :
    task.action === 'card_adjust_credit_limit' ? !!creditLimit : true);
  const subtitle = editing ? '填写后生成确认信息 · 尚未执行'
    : open ? `${operation!.permission_level === 'red' ? '需确认 + 验证码' : '需本人确认'} · 尚未执行` : operation?.message;

  return <article className="card nova-transfer-card nova-card-operation" aria-label="卡片操作任务">
    <div className="nova-transfer-card-head"><div>
      <strong>{ACTION_LABELS[task.action]}{card ? ` · ${card.card_number}` : ''}</strong>
      <small>{subtitle}</small>
    </div>{operation && !editing && <span className={`card-level card-level-${operation.permission_level}`}>{operation.permission_level === 'red' ? '🔴' : '🟡'}</span>}</div>

    {editing && <form className="nova-collection-form" onSubmit={event => { event.preventDefault(); void submitForm(); }}>
      {task.action === 'card_apply_virtual' ? <label>关联借记账户<select value={accountId} disabled={off} onChange={e => setAccountId(e.target.value)}>
        <option value="">请选择账户</option>
        {eligibleAccounts.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>
      : !task.card_id && <label>选择卡片<select value={cardId} disabled={off} onChange={e => chooseCard(e.target.value)}>
        <option value="">{cards.length ? '请选择卡片' : '没有可办理此操作的卡片'}</option>
        {cards.map(c => <option key={c.card_id} value={c.card_id}>{c.card_type === 'CREDIT' ? '信用卡' : c.is_virtual ? '虚拟借记卡' : '借记卡'} · {c.card_number} · {STATUS_LABELS[c.status]}</option>)}</select></label>}
      {task.action === 'card_set_transaction_limits' && <>
        <label>单笔限额（元{card ? `，银行上限 ${yuan(card.single_txn_cap_fen)}` : ''}）<input type="number" min="0.01" step="0.01" value={singleLimit} disabled={off} onChange={e => setSingleLimit(e.target.value)} /></label>
        <label>单日限额（元{card ? `，银行上限 ${yuan(card.daily_txn_cap_fen)}` : ''}）<input type="number" min="0.01" step="0.01" value={dailyLimit} disabled={off} onChange={e => setDailyLimit(e.target.value)} /></label>
        <p className="hint">只调低限额需本人确认；调高任一项还需验证码。</p></>}
      {task.action === 'card_adjust_credit_limit' && <>
        <label>新信用额度（元{card ? `，核定上限 ${yuan(card.credit_limit_cap_fen)}，不低于已用 ${yuan(card.used_credit_fen)}；当前 ${yuan(card.credit_limit_fen)}` : ''}）
          <input type="number" min="0.01" step="0.01" value={creditLimit} disabled={off} placeholder="请输入新额度" onChange={e => setCreditLimit(e.target.value)} /></label>
        <p className="hint">降额需本人确认；提额还需验证码。</p></>}
      {task.action === 'card_set_channels' && <fieldset disabled={off}><legend>交易渠道</legend>
        {CHANNELS.map(([key, label]) => <label key={key} className="check-option"><input type="checkbox" checked={channels[key]} onChange={e => setChannels({ ...channels, [key]: e.target.checked })} /> {label}</label>)}
        <p className="hint">只关闭渠道需本人确认；打开任一渠道还需验证码。</p></fieldset>}
      {ACTION_HINTS[task.action] && <p className="hint">{ACTION_HINTS[task.action]}</p>}
      <p className="hint">提交后由银行校验并返回变更明细，尚不会执行。</p>
      <div className="nova-transfer-actions">
        {operation ? <button type="button" className="secondary" disabled={off} onClick={() => setEditing(false)}>返回确认信息</button>
          : <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>不办理</button>}
        <button type="submit" disabled={off || !ready}>{busy ? '正在校验…' : '生成确认信息'}</button>
      </div>
    </form>}

    {!editing && operation && <>
      {open && <p>{operation.message}</p>}
      {operation.changes.length > 0 && <ul className="history">{operation.changes.map(change => <li key={change.field}>
        <span>{change.label}</span><span>{changeValue(change, change.before)} → <strong>{changeValue(change, change.after)}</strong></span>
      </li>)}</ul>}
      {open && ACTION_HINTS[operation.action] && <p className="hint">{ACTION_HINTS[operation.action]}</p>}
      {open && <small className="hint">以上数值均由银行返回；确认有效期至（北京时间）{beijingTime(operation.expires_at)}。NOVA 不能代为确认或输入验证码。</small>}
      {operation.status === 'COMPLETED' && operation.result && <p>当前状态：{STATUS_LABELS[operation.result.status]} · 单笔 {yuan(operation.result.single_txn_limit_fen)} · 单日 {yuan(operation.result.daily_txn_limit_fen)}</p>}
      {allowed.includes('verify') && <div className="nova-transfer-verify">
        <SecondFactor purpose="card" referenceId={operation.operation_id} factor={factor} onChange={setFactor} disabled={off} />
        <input inputMode="numeric" maxLength={6} value={code} placeholder="6 位验证码" disabled={off}
          onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          onKeyDown={e => e.key === 'Enter' && code.length === 6 && factor && run(() => bank.verifyCardOperation(operation, code, factor))} />
      </div>}
      <div className="nova-transfer-actions">
        {allowed.includes('confirm') && EDITABLE.has(operation.action) && <button className="secondary" disabled={off} onClick={() => setEditing(true)}>修改</button>}
        {allowed.includes('cancel') && <button className="secondary" disabled={off} onClick={() => run(() => bank.cancelCardOperation(operation))}>取消</button>}
        {allowed.includes('confirm') && <button disabled={off} onClick={() => run(() => bank.confirmCardOperation(operation))}>{busy ? '处理中…' : operation.requires_mfa ? '确认，下一步验证' : '确认执行'}</button>}
        {allowed.includes('verify') && <button disabled={off || !factor || code.length !== 6} onClick={() => factor && run(() => bank.verifyCardOperation(operation, code, factor))}>{busy ? '验证中…' : '提交验证'}</button>}
        {!open && ['EXPIRED', 'CANCELLED', 'FAILED'].includes(operation.status) && <button className="secondary" disabled={off} onClick={() => setEditing(true)}>重新填写</button>}
      </div>
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </article>;
}
