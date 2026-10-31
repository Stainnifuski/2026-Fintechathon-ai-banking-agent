import {requestFeedback} from '../utils/requestFeedback';
import {useTaskField} from './NovaTaskPresentation';
import { FormEvent, useEffect, useState } from 'react';
import * as bank from '../services/bank';
import { cancelNovaTaskCard, getNovaTaskSession, NovaTaskSession, NovaTransferTask, saveNovaTaskForm } from '../services/nova';
import { SecondFactor } from './SecondFactor';
import { formatYuan, formatYuanText } from '../utils/money';
import { beijingInput, beijingTime, fromBeijingInput } from '../utils/time';
import { cardTypeLabel } from '../utils/accountType';

type Props = {
  manual?:boolean;
  task: NovaTransferTask; session: NovaTaskSession; accounts: bank.Account[]; focused: boolean;
  disabled: boolean; onUpdated: (session: NovaTaskSession) => void; onFocus: () => void;
  onSelectCandidate: (candidateId: string) => void; onTransferActivity?: () => void;
};

const errorText=requestFeedback;

export default function NovaTransferCard({ task, session, accounts, focused, disabled,manual,
  onUpdated, onFocus, onSelectCandidate, onTransferActivity }: Props) {
  const [recipient, setRecipient] = useTaskField('recipient',task.form.recipient);
  const [amount, setAmount] = useTaskField('amount',task.form.amount_yuan);
  const [phone, setPhone] = useTaskField('phone',task.form.phone_masked || '');
  const [card, setCard] = useTaskField('card',task.form.card_masked || '');
  const [clearPhone, setClearPhone] = useTaskField('clearPhone',false);
  const [clearCard, setClearCard] = useTaskField('clearCard',false);
  const [payer, setPayer] = useTaskField('payer',task.form.payer_account_id);
  const [note, setNote] = useTaskField('note',task.form.note);
  const [executionMode, setExecutionMode] = useTaskField<'instant' | 'scheduled'>('executionMode',task.form.execution_mode ?? 'instant');
  const [scheduledAt, setScheduledAt] = useTaskField('scheduledAt',task.form.scheduled_at ? beijingInput(new Date(task.form.scheduled_at)) : '');
  const [snapshot, setSnapshot] = useState<bank.TaskSnapshot | null>(null);
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [code, setCode] = useState('');
  useEffect(()=>{const clear=()=>setCode('');window.addEventListener('nova-clear-verification',clear);return()=>window.removeEventListener('nova-clear-verification',clear);},[]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const visible = snapshot || task.task || null;
  const succeeded = task.status === 'SUCCEEDED' || visible?.outcome === 'SUCCEEDED';
  const scheduled = visible?.phase === 'WAITING_EXECUTION' && !!visible.scheduled_at;
  const [collapsed, setCollapsed] = useState(succeeded || scheduled);
  const formSignature = JSON.stringify(task.form);
  useEffect(() => {
    setRecipient(task.form.recipient); setAmount(task.form.amount_yuan);
    setPhone(task.form.phone_masked || ''); setCard(task.form.card_masked || '');
    setClearPhone(false); setClearCard(false);
    setPayer(task.form.payer_account_id); setNote(task.form.note);
    setExecutionMode(task.form.execution_mode ?? 'instant');
    setScheduledAt(task.form.scheduled_at ? beijingInput(new Date(task.form.scheduled_at)) : '');
  }, [formSignature]);
  useEffect(() => { if (succeeded || scheduled) setCollapsed(true); }, [succeeded, scheduled]);
  useEffect(() => { setSnapshot(null); }, [task.task?.state_version, task.task?.plan_version, task.task?.outcome]);
  const ended = task.status === 'CANCELLED' || visible?.phase === 'FINISHED';
  const newPhone = phone !== (task.form.phone_masked || '') && !!phone;
  const newCard = card !== (task.form.card_masked || '') && !!card;
  const dirty = recipient !== task.form.recipient || amount !== task.form.amount_yuan ||
    phone !== (task.form.phone_masked || '') || card !== (task.form.card_masked || '') ||
    clearPhone || clearCard || payer !== task.form.payer_account_id || note !== task.form.note ||
    executionMode !== (task.form.execution_mode ?? 'instant') ||
    (executionMode === 'scheduled' && scheduledAt !== (task.form.scheduled_at ? beijingInput(new Date(task.form.scheduled_at)) : ''));
  const canCancel = !ended && (!visible || visible.allowed_actions.includes('cancel'));
  const canSave = !ended && (!visible || visible.allowed_actions.includes('modify'));

  async function refresh() {
    const updated = await getNovaTaskSession(session.session_id);
    onUpdated(updated);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (disabled || busy || !canSave) return;
    if (!recipient.trim()) { setError('请填写收款人户名或备注名'); return; }
    if (!amount.trim() || !Number.isFinite(Number(amount)) || Number(amount) <= 0) { setError('请填写有效的转账金额'); return; }
    if (!dirty) return;
    if (executionMode === 'scheduled' && !scheduledAt) { setError('请选择定时执行时间'); return; }
    if (executionMode === 'scheduled' && new Date(fromBeijingInput(scheduledAt)).getTime() <= Date.now()) {
      setError('定时执行时间必须晚于当前时间'); return;
    }
    setBusy(true); setError(''); setNotice('');
    try {
      const latest=manual?await getNovaTaskSession(session.session_id):session;
      if(manual&&JSON.stringify(latest.tasks.find(item=>item.chat_task_id===task.chat_task_id)?.form)!==JSON.stringify(task.form))throw new Error('原表单已变化，请重新读取后核对；本次未覆盖。');
      const updated = await saveNovaTaskForm(session.session_id, task.chat_task_id, latest.session_version, {
        recipient, amount_yuan: amount, phone: newPhone ? phone.trim() : null, card: newCard ? card.trim() : null,
        clear_phone: clearPhone || (!!task.form.phone_masked && !phone),
        clear_card: clearCard || (!!task.form.card_masked && !card), payer_account_id: payer, note,
        execution_mode: executionMode,
        scheduled_at: executionMode === 'scheduled' ? fromBeijingInput(scheduledAt) : null,
      });
      onUpdated(updated);
      setNotice('信息已更新，请核对卡片中的确认信息；缺少的内容仍需补充。');
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }

  async function close() {
    if (disabled || busy || !canCancel) return;
    setBusy(true); setError('');
    try {
      const latest=manual?await getNovaTaskSession(session.session_id):session;
      onUpdated(await cancelNovaTaskCard(session.session_id, task.chat_task_id, latest.session_version));
    } catch (failure) { setError(errorText(failure)); await refresh().catch(() => {}); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!visible || disabled || busy || dirty) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const latest = await bank.getTask(visible.task_id);
      if (latest.state_version !== visible.state_version || latest.plan_version !== visible.plan_version) {
        setSnapshot(latest);
        setNotice('转账计划已更新，请重新核对卡片后再确认。');
        await refresh();
        return;
      }
      let next = await bank.confirmTask(latest, crypto.randomUUID());
      if (next.allowed_actions.includes('accept')) next = await bank.acceptTask(next, crypto.randomUUID());
      setSnapshot(next); setNotice(next.message);
      await refresh();
      if (next.phase === 'FINISHED') onTransferActivity?.();
    } catch (failure) {
      setError(errorText(failure));
      const latest = await bank.getTask(visible.task_id).catch(() => null);
      if (latest) setSnapshot(latest);
      await refresh().catch(() => {});
    } finally { setBusy(false); }
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!visible || !factor || code.length !== 6 || disabled || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      let next = await bank.getTask(visible.task_id);
      if (!next.allowed_actions.includes('verify')) throw new Error('任务状态已变化，请刷新后重试。');
      if (next.state_version !== visible.state_version || next.plan_version !== visible.plan_version) {
        setSnapshot(next); setNotice('转账计划已更新，请核对后重新验证。');
        await refresh();
        return;
      }
      next = await bank.verifyTask(next, crypto.randomUUID(), code, factor);
      if (next.allowed_actions.includes('accept')) next = await bank.acceptTask(next, crypto.randomUUID());
      setSnapshot(next); setNotice(next.message); setCode('');
      await refresh();
      if (next.phase === 'FINISHED') onTransferActivity?.();
    } catch (failure) {
      setError(errorText(failure));
      const latest = await bank.getTask(visible.task_id).catch(() => null);
      if (latest) setSnapshot(latest);
    } finally { setBusy(false); }
  }

  async function reconcile() {
    if (!visible || disabled || busy) return;
    setBusy(true); setError('');
    try {
      const latest = await bank.getTask(visible.task_id);
      const next = await bank.reconcileTask(latest, crypto.randomUUID());
      setSnapshot(next); setNotice(next.message); await refresh();
      if (next.phase === 'FINISHED') onTransferActivity?.();
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }

  return <article className={`card nova-transfer-card${focused?' is-focused':''}`} aria-label={`转账任务：${task.recipient || '待填写收款人'}`} onFocusCapture={onFocus} onPointerDown={onFocus}>
    <div className="nova-transfer-card-head"><div><strong>转账任务</strong>
      <small>{task.recipient || '待填写收款人'} · {task.form.amount_yuan ? formatYuanText(task.form.amount_yuan) : '待填写金额'}
        {succeeded ? ' · 已完成' : scheduled ? ' · 已预约' : task.missing_fields.length ? ' · 待补充' : ' · 待核对'}</small></div>
      <div className="nova-transfer-card-controls"><button type="button" className="secondary nova-transfer-toggle"
        aria-expanded={!collapsed} aria-label={collapsed ? '展开转账任务' : '折叠转账任务'}
        onClick={() => setCollapsed(value => !value)}>{collapsed ? '展开' : '折叠'}</button>
      {canCancel && <button type="button" className="icon-button" aria-label="取消这笔转账任务" title="取消这笔任务"
        disabled={disabled || busy} onClick={() => void close()}>×</button>}
      </div>
    </div>
    {!collapsed && <>
    <p className="hint">{visible?.message || (task.missing_fields.length ? `还需：${task.missing_fields.join('、')}` : '请核对转账信息')}</p>
    <form onSubmit={event => void save(event)} className="nova-transfer-form">
      <label>收款人户名或备注名<input value={recipient} maxLength={100} disabled={disabled || busy || !canSave}
        onChange={event => setRecipient(event.target.value)} placeholder="输入真实户名或已保存的备注名" /></label>
      <label>金额（人民币元）<input value={amount} inputMode="decimal" maxLength={30} disabled={disabled || busy || !canSave}
        onChange={event => setAmount(event.target.value)} placeholder="例如 100.00" /></label>
      <label>收款人手机号{task.form.phone_masked && !clearPhone && <small>已保存尾号，点击输入框可更换</small>}
        <input value={phone} inputMode="tel" autoComplete="off" maxLength={32} disabled={disabled || busy || !canSave || newCard}
          onFocus={() => { if (phone === task.form.phone_masked) setPhone(''); }}
          onChange={event => { setPhone(event.target.value); if (event.target.value) setClearPhone(false); }} placeholder="新收款人填写完整手机号" /></label>
      {task.form.phone_masked && !clearPhone && canSave && <button type="button" className="secondary" disabled={disabled || busy} onClick={() => { setClearPhone(true); setPhone(''); }}>清除已保存手机号</button>}
      <label>收款人银行卡号{task.form.card_masked && !clearCard && <small>已保存尾号，点击输入框可更换</small>}
        <input value={card} inputMode="numeric" autoComplete="off" maxLength={32} disabled={disabled || busy || !canSave || newPhone}
          onFocus={() => { if (card === task.form.card_masked) setCard(''); }}
          onChange={event => { setCard(event.target.value); if (event.target.value) setClearCard(false); }} placeholder="或填写完整银行卡号" /></label>
      {task.form.card_masked && !clearCard && canSave && <button type="button" className="secondary" disabled={disabled || busy} onClick={() => { setClearCard(true); setCard(''); }}>清除已保存银行卡号</button>}
      <label>付款账户<select value={payer} disabled={disabled || busy || !canSave} onChange={event => setPayer(event.target.value)}>
        <option value="">自动选择可用账户</option>
        {accounts.map(account => <option key={account.account_id} value={account.account_id}>
          {cardTypeLabel(account.account_type)} · 尾号 {account.card_number.slice(-4)}
        </option>)}
      </select></label>
      <label>备注（可选）<input value={note} maxLength={256} disabled={disabled || busy || !canSave}
        onChange={event => setNote(event.target.value)} placeholder="转账备注" /></label>
      <label>执行方式<select value={executionMode} disabled={disabled || busy || !canSave}
        onChange={event => setExecutionMode(event.target.value as 'instant' | 'scheduled')}><option value="instant">立即转账</option><option value="scheduled">定时转账</option></select></label>
      {executionMode === 'scheduled' && <>
        <label>执行时间（北京时间）<input type="datetime-local" required min={beijingInput(new Date())}
          value={scheduledAt} disabled={disabled || busy || !canSave} onChange={event => setScheduledAt(event.target.value)} />
          <small>到达设定时间后才执行；确认前可修改。</small></label>
      </>}
      {canSave && <button type="submit" disabled={disabled || busy || (!dirty && !!visible)}>生成确认信息</button>}
    </form>
    {task.candidates.length > 0 && <div className="nova-transfer-candidates"><p>请选择匹配的账户：</p>
      {task.candidates.map(candidate => <button type="button" key={candidate.candidate_id} className="secondary"
        disabled={disabled || busy} onClick={() => onSelectCandidate(candidate.candidate_id)}>{candidate.label}</button>)}
    </div>}
    {visible?.confirmation_card && <div className="nova-transfer-review">
      <strong>请核对后确认</strong>
      <p>从 {visible.confirmation_card.payer_card_masked || '付款账户'} 转给 {visible.confirmation_card.recipient_name}<br />
        收款账户：{visible.confirmation_card.recipient_card_masked || visible.confirmation_card.recipient_phone_masked || '请核对收款人'}<br />
        金额：{formatYuan(visible.confirmation_card.amount)} · 备注：{visible.confirmation_card.note || '无'}</p>
      {visible.scheduled_at && <p>计划执行时间：{beijingTime(visible.scheduled_at)}（北京时间）</p>}
      <p className="hint">风险级别：{visible.confirmation_card.risk_level}。{visible.scheduled_at ? '确认后等待计划时间执行' : '点击确认后将按银行规则执行'}；高风险转账还需验证码。</p>
    </div>}
    <div className="nova-transfer-actions">
      {visible?.allowed_actions.includes('confirm') && <button type="button" disabled={disabled || busy || dirty}
        onClick={() => void confirm()}>{visible.scheduled_at ? '确认定时转账' : '确认转账'}</button>}
      {visible?.allowed_actions.includes('reconcile') && <button type="button" disabled={disabled || busy}
        onClick={() => void reconcile()}>核对银行结果</button>}
    </div>
    {visible?.allowed_actions.includes('verify') && <form className="nova-transfer-verify" onSubmit={event => void verify(event)}>
      <strong>身份验证</strong><SecondFactor purpose="transfer" referenceId={visible.task_id} factor={factor} onChange={setFactor} disabled={disabled || busy} />
      <input aria-label="转账验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
        value={code} disabled={disabled || busy} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="6 位验证码" />
      <button type="submit" disabled={disabled || busy || !factor || code.length !== 6}>{visible.scheduled_at ? '验证并安排定时转账' : '验证并执行'}</button>
    </form>}
    {notice && <p className="hint" role="status">{notice}</p>}
    {error && <p className="nova-error" role="alert">{error}</p>}
    </>}
  </article>;
}
