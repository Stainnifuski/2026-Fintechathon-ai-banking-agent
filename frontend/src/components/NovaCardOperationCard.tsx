import {requestFeedback} from '../utils/requestFeedback';
import {useTaskField,useTaskPrefill} from './NovaTaskPresentation';
import { useEffect, useState } from 'react';
import * as bank from '../services/bank';
import { dismissNovaCardTask, getNovaTaskSession, linkNovaCardTask, type NovaChatCardTask } from '../services/nova';
import { beijingTime } from '../utils/time';
import { SecondFactor } from './SecondFactor';
import { formatFen } from '../utils/money';
import { ACTION_HINTS, ACTION_LABELS, CARD_ACTION_OPTIONS, CHANNELS, STATUS_LABELS,
  availableCardActions, initialCardAction } from './CardManagement/cardActions';

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const EDITABLE = new Set<bank.CardAction>(['card_apply_virtual', 'card_set_transaction_limits', 'card_adjust_credit_limit', 'card_set_channels']);
const yuan = (fen?: number | null) => fen == null ? '-' : formatFen(fen);
const fenText = (fen?: number | null) => fen == null ? '' : (fen / 100).toFixed(2);
const changeValue = (change: bank.CardChange, value?: boolean | number | string | null) => {
  if (value == null) return '无';
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  if (change.unit === 'fen' && typeof value === 'number') return yuan(value);
  return STATUS_LABELS[String(value)] ?? String(value);
};
const errorText=requestFeedback;
type Channels = Record<bank.CardChannelSwitch, boolean>;

/** NOVA 识别出的卡片操作在聊天中以表单办理：本人填写/修改 → 生成确认信息 → 本人确认（及验证）。 */
export default function NovaCardOperationCard({ task, sessionId, accounts, disabled, onUpdated, onActivity }: {
  task: NovaChatCardTask; sessionId: string; accounts: bank.Account[]; disabled: boolean;
  onUpdated: (tasks: NovaChatCardTask[]) => void; onActivity?: () => void;
}) {
  const prefill = task.prefill;
  const [operation, setOperation] = useState<bank.CardOperation | null>(task.operation ?? null);
  const [editing, setEditing] = useState(!task.operation);
  const [activeAction, setActiveAction] = useState<bank.CardAction>(initialCardAction(task.card, task.action));
  const [cards, setCards] = useState<bank.BankCard[]>(task.card ? [task.card] : []);
  const [cardId, setCardId] = useTaskField('cardId',task.card_id ?? '');
  const eligibleAccounts = accounts.filter(a => ['savings', 'checking'].includes(a.account_type));
  const [accountId, setAccountId] = useTaskField('accountId',String(prefill.account_id ?? ''));
  const [singleLimit, setSingleLimit] = useTaskField('singleLimit',String(prefill.single_txn_limit ?? fenText(task.card?.single_txn_limit_fen)));
  const [dailyLimit, setDailyLimit] = useTaskField('dailyLimit',String(prefill.daily_txn_limit ?? fenText(task.card?.daily_txn_limit_fen)));
  const [creditLimit, setCreditLimit] = useTaskField('creditLimit',String(prefill.credit_limit ?? fenText(task.card?.credit_limit_fen)));
  const [channels, setChannels] = useTaskField<Channels>('channels',() => Object.fromEntries(CHANNELS.map(([key]) =>
    [key, typeof prefill[key] === 'boolean' ? prefill[key] as boolean : task.card?.[key] ?? false])) as Channels);
  const [code, setCode] = useState('');
  useEffect(()=>{const clear=()=>setCode('');window.addEventListener('nova-clear-verification',clear);return()=>window.removeEventListener('nova-clear-verification',clear);},[]);
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(task.notice ?? '');
  useTaskPrefill({...prefill,card_id:task.card_id},{card_id:v=>setCardId(String(v??'')),account_id:v=>setAccountId(String(v??'')),single_txn_limit:v=>setSingleLimit(String(v??'')),daily_txn_limit:v=>setDailyLimit(String(v??'')),credit_limit:v=>setCreditLimit(String(v??'')),...Object.fromEntries(CHANNELS.map(([key])=>[key,(v:unknown)=>{if(typeof v==='boolean')setChannels(old=>({...old,[key]:v}));}]))});

  const taskPrefillKey=JSON.stringify([task.entry_id,task.action,task.card_id,task.prefill,task.card,task.notice,
    task.operation?.operation_id,task.operation?.status]);
  useEffect(() => {
    setOperation(task.operation ?? null);
    setEditing(!task.operation);
    setActiveAction(initialCardAction(task.card, task.action));
    setCards(task.card ? [task.card] : []);
    setCardId(task.card_id ?? '');
    setAccountId(String(task.prefill.account_id ?? ''));
    setSingleLimit(String(task.prefill.single_txn_limit ?? fenText(task.card?.single_txn_limit_fen)));
    setDailyLimit(String(task.prefill.daily_txn_limit ?? fenText(task.card?.daily_txn_limit_fen)));
    setCreditLimit(String(task.prefill.credit_limit ?? fenText(task.card?.credit_limit_fen)));
    setChannels(Object.fromEntries(CHANNELS.map(([key]) =>
      [key, typeof task.prefill[key] === 'boolean' ? task.prefill[key] as boolean : task.card?.[key] ?? false])) as Channels);
    setError(task.notice ?? '');
  }, [taskPrefillKey]);
  // 未指定卡片时，在表单中列出本人当前允许该操作的卡片供选择。
  useEffect(() => {
    if (task.card_id || task.action === 'card_apply_virtual') return;
    let live = true;
    bank.listCards().then(items => { if (live) setCards(items.filter(item => availableCardActions(item).includes(task.action))); })
      .catch(err => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [task.entry_id]);
  useEffect(() => { if (!accountId && eligibleAccounts[0]) setAccountId(eligibleAccounts[0].account_id); }, [accounts]);

  const card = cards.find(item => item.card_id === cardId) ?? task.card ?? operation?.card ?? null;
  const canSwitch = !operation || operation.allowed_actions.includes('confirm');
  const cardActions = card ? availableCardActions(card) : [];
  const actionAvailable = !card || cardActions.includes(activeAction);
  const switchAction = (action: bank.CardAction) => {
    if (busy || disabled || !canSwitch || action === activeAction) return;
    setActiveAction(action);
    setEditing(true);
    setError('');
  };
  const chooseCard = (id: string) => {
    setCardId(id);
    const next = cards.find(item => item.card_id === id);
    if (!next) return;
    setActiveAction(initialCardAction(next, activeAction));
    if (!prefill.single_txn_limit) setSingleLimit(fenText(next.single_txn_limit_fen));
    if (!prefill.daily_txn_limit) setDailyLimit(fenText(next.daily_txn_limit_fen));
    if (!prefill.credit_limit) setCreditLimit(fenText(next.credit_limit_fen));
    setChannels(Object.fromEntries(CHANNELS.map(([key]) =>
      [key, typeof prefill[key] === 'boolean' ? prefill[key] as boolean : next[key]])) as Channels);
  };

  const run = async (step: () => Promise<bank.CardOperation>) => {
    if (busy || disabled) return;
    setBusy(true); setError('');
    try {
      const result = await step();
      setOperation(result); setCode('');
      if (result.status === 'COMPLETED') {
        onActivity?.();
        try {
          const latest = await getNovaTaskSession(sessionId);
          onUpdated(latest.card_operations ?? []);
        } catch {
          setError('操作已完成，但卡片状态刷新失败；请重新打开对话核对。');
        }
      }
    } catch (err) {
      setError(errorText(err)); setCode('');
      // 授权可能已推进（过期、验证锁定、银行拒绝），以服务端为准。
      if (operation) { const latest = await bank.getCardOperation(operation.operation_id).catch(() => null); if (latest) setOperation(latest); }
    } finally { setBusy(false); }
  };

  const submitForm = async () => {
    if (busy || disabled || !actionAvailable) return;
    const payload: bank.CardOperationRequest = { operation_id: newId(), action: activeAction };
    if (activeAction === 'card_apply_virtual') payload.account_id = accountId;
    else payload.card_id = cardId;
    if (activeAction === 'card_set_transaction_limits') { payload.single_txn_limit = singleLimit; payload.daily_txn_limit = dailyLimit; }
    if (activeAction === 'card_adjust_credit_limit') payload.credit_limit = creditLimit;
    if (activeAction === 'card_set_channels') Object.assign(payload, channels);
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
  const ready = actionAvailable && (activeAction === 'card_apply_virtual' ? !!accountId : !!cardId && (
    activeAction === 'card_set_transaction_limits' ? !!singleLimit && !!dailyLimit :
    activeAction === 'card_adjust_credit_limit' ? !!creditLimit : true));
  const subtitle = editing ? '填写后生成确认信息 · 尚未执行'
    : open ? `${operation!.permission_level === 'red' ? '需确认 + 验证码' : '需本人确认'} · 尚未执行` : operation?.message;

  return <article className="card nova-transfer-card nova-card-operation" aria-label="卡片操作任务">
    <div className="nova-transfer-card-head"><div>
      <strong>{card ? `卡片操作 · ${card.card_number}` : ACTION_LABELS[activeAction]}</strong>
      <small>{subtitle}</small>
    </div>{operation && !editing && <span className={`card-level card-level-${operation.permission_level}`}>{operation.permission_level === 'red' ? '🔴' : '🟡'}</span>}</div>

    {card && <div className="nova-card-current" aria-label="当前卡片设置">
      <span>{card.card_type === 'CREDIT' ? '信用卡' : card.is_virtual ? '虚拟借记卡' : '借记卡'} · {STATUS_LABELS[card.status]}</span>
      <span>单笔 {yuan(card.single_txn_limit_fen)} · 单日 {yuan(card.daily_txn_limit_fen)}</span>
      {card.card_type === 'CREDIT' && <span>信用额度 {yuan(card.credit_limit_fen)}</span>}
    </div>}
    {card && cardActions.length > 0 && <nav className="nova-card-actions" aria-label="这张卡可办理的操作">
      {CARD_ACTION_OPTIONS.filter(({ action }) => cardActions.includes(action)).map(({ action, label, danger }) =>
        <button key={action} type="button" className={`secondary${danger ? ' card-danger' : ''}`}
          aria-pressed={activeAction === action} disabled={off || !canSwitch}
          onClick={() => switchAction(action)}>{label}</button>)}
    </nav>}
    {card && editing && !actionAvailable && <div className="nova-card-unavailable">
      <p>{cardActions.length ? '当前栏目已不可办理，请选择上方可用操作。' : '这张卡当前没有可办理的操作。'}</p>
      <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>关闭任务卡</button>
    </div>}
    {editing && actionAvailable && <form className="nova-collection-form" onSubmit={event => { event.preventDefault(); void submitForm(); }}>
      <p className="hint">{ACTION_LABELS[activeAction]} · 核对后生成确认信息</p>
      {activeAction === 'card_apply_virtual' ? <label>关联借记账户<select value={accountId} disabled={off} onChange={e => setAccountId(e.target.value)}>
        <option value="">请选择账户</option>
        {eligibleAccounts.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>
      : !task.card_id && <label>选择卡片<select value={cardId} disabled={off} onChange={e => chooseCard(e.target.value)}>
        <option value="">{cards.length ? '请选择卡片' : '没有可办理此操作的卡片'}</option>
        {cards.map(c => <option key={c.card_id} value={c.card_id}>{c.card_type === 'CREDIT' ? '信用卡' : c.is_virtual ? '虚拟借记卡' : '借记卡'} · {c.card_number} · {STATUS_LABELS[c.status]}</option>)}</select></label>}
      {activeAction === 'card_set_transaction_limits' && <>
        <label>单笔限额（元{card ? `，银行上限 ${yuan(card.single_txn_cap_fen)}` : ''}）<input type="number" min="0.01" step="0.01" value={singleLimit} disabled={off} onChange={e => setSingleLimit(e.target.value)} /></label>
        <label>单日限额（元{card ? `，银行上限 ${yuan(card.daily_txn_cap_fen)}` : ''}）<input type="number" min="0.01" step="0.01" value={dailyLimit} disabled={off} onChange={e => setDailyLimit(e.target.value)} /></label>
        <p className="hint">只调低限额需本人确认；调高任一项还需验证码。</p></>}
      {activeAction === 'card_adjust_credit_limit' && <>
        <label>新信用额度（元{card ? `，核定上限 ${yuan(card.credit_limit_cap_fen)}，不低于已用 ${yuan(card.used_credit_fen)}；当前 ${yuan(card.credit_limit_fen)}` : ''}）
          <input type="number" min="0.01" step="0.01" value={creditLimit} disabled={off} placeholder="请输入新额度" onChange={e => setCreditLimit(e.target.value)} /></label>
        <p className="hint">降额需本人确认；提额还需验证码。</p></>}
      {activeAction === 'card_set_channels' && <fieldset disabled={off}><legend>交易渠道</legend>
        {CHANNELS.map(([key, label]) => <label key={key} className="check-option"><input type="checkbox" checked={channels[key]} onChange={e => setChannels({ ...channels, [key]: e.target.checked })} /> {label}</label>)}
        <p className="hint">只关闭渠道需本人确认；打开任一渠道还需验证码。</p></fieldset>}
      {ACTION_HINTS[activeAction] && <p className="hint">{ACTION_HINTS[activeAction]}</p>}
      <p className="hint">提交后由银行校验并返回变更明细，尚不会执行。</p>
      <div className="nova-transfer-actions">
        {operation ? <><button type="button" className="secondary" disabled={off}
            onClick={() => { setActiveAction(operation.action); setEditing(false); }}>返回确认信息</button>
          <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>取消任务</button></>
          : <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>取消任务</button>}
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
        {allowed.includes('cancel') && <button className="secondary" disabled={off} onClick={() => void dismiss()}>取消任务</button>}
        {allowed.includes('confirm') && <button disabled={off} onClick={() => run(() => bank.confirmCardOperation(operation))}>{busy ? '处理中…' : operation.requires_mfa ? '确认，下一步验证' : '确认执行'}</button>}
        {allowed.includes('verify') && <button disabled={off || !factor || code.length !== 6} onClick={() => factor && run(() => bank.verifyCardOperation(operation, code, factor))}>{busy ? '验证中…' : '提交验证'}</button>}
        {!open && ['EXPIRED', 'CANCELLED', 'FAILED'].includes(operation.status) && <button className="secondary" disabled={off} onClick={() => setEditing(true)}>重新填写</button>}
      </div>
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </article>;
}
