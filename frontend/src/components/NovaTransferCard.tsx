import { FormEvent, useEffect, useState } from 'react';
import * as bank from '../services/bank';
import { cancelNovaTaskCard, getNovaTaskSession, NovaTaskSession, NovaTransferTask, saveNovaTaskForm } from '../services/nova';
import { SecondFactor } from './SecondFactor';

type Props = {
  task: NovaTransferTask; session: NovaTaskSession; accounts: bank.Account[]; focused: boolean;
  disabled: boolean; onUpdated: (session: NovaTaskSession) => void; onFocus: () => void;
  onSelectCandidate: (candidateId: string) => void; onTransferActivity?: () => void;
};

const errorText = (error: unknown) => {
  const detail = (error as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : detail?.message ||
    (error instanceof Error ? error.message : '操作失败，请刷新任务后重试。');
};

export default function NovaTransferCard({ task, session, accounts, focused, disabled,
  onUpdated, onFocus, onSelectCandidate, onTransferActivity }: Props) {
  const [recipient, setRecipient] = useState(task.form.recipient);
  const [amount, setAmount] = useState(task.form.amount_yuan);
  const [phone, setPhone] = useState(task.form.phone_masked || '');
  const [card, setCard] = useState(task.form.card_masked || '');
  const [clearPhone, setClearPhone] = useState(false);
  const [clearCard, setClearCard] = useState(false);
  const [payer, setPayer] = useState(task.form.payer_account_id);
  const [note, setNote] = useState(task.form.note);
  const [snapshot, setSnapshot] = useState<bank.TaskSnapshot | null>(null);
  const [factor, setFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const visible = snapshot || task.task || null;
  const succeeded = task.status === 'SUCCEEDED' || visible?.outcome === 'SUCCEEDED';
  const [collapsed, setCollapsed] = useState(succeeded);
  const formSignature = JSON.stringify(task.form);
  useEffect(() => {
    setRecipient(task.form.recipient); setAmount(task.form.amount_yuan);
    setPhone(task.form.phone_masked || ''); setCard(task.form.card_masked || '');
    setClearPhone(false); setClearCard(false);
    setPayer(task.form.payer_account_id); setNote(task.form.note);
  }, [formSignature]);
  useEffect(() => { if (succeeded) setCollapsed(true); }, [succeeded]);
  useEffect(() => { setSnapshot(null); }, [task.task?.state_version, task.task?.plan_version, task.task?.outcome]);
  const ended = task.status === 'CANCELLED' || visible?.phase === 'FINISHED';
  const newPhone = phone !== (task.form.phone_masked || '') && !!phone;
  const newCard = card !== (task.form.card_masked || '') && !!card;
  const dirty = recipient !== task.form.recipient || amount !== task.form.amount_yuan ||
    phone !== (task.form.phone_masked || '') || card !== (task.form.card_masked || '') ||
    clearPhone || clearCard || payer !== task.form.payer_account_id || note !== task.form.note;
  const canCancel = !ended && (!visible || visible.allowed_actions.includes('cancel'));
  const canSave = !ended && (!visible || visible.allowed_actions.includes('modify'));

  async function refresh() {
    const updated = await getNovaTaskSession(session.session_id);
    onUpdated(updated);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (disabled || busy || !dirty || !canSave) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const updated = await saveNovaTaskForm(session.session_id, task.chat_task_id, session.session_version, {
        recipient, amount_yuan: amount, phone: newPhone ? phone.trim() : null, card: newCard ? card.trim() : null,
        clear_phone: clearPhone || (!!task.form.phone_masked && !phone),
        clear_card: clearCard || (!!task.form.card_masked && !card), payer_account_id: payer, note,
      });
      onUpdated(updated);
      setNotice('卡片信息已保存，请核对最新计划。');
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }

  async function close() {
    if (disabled || busy || !canCancel) return;
    setBusy(true); setError('');
    try {
      onUpdated(await cancelNovaTaskCard(session.session_id, task.chat_task_id, session.session_version));
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
      <small>{task.recipient || '待填写收款人'} · {task.form.amount_yuan ? `¥${task.form.amount_yuan}` : '待填写金额'}
        {succeeded ? ' · 已完成' : task.missing_fields.length ? ' · 待补充' : ' · 待核对'}</small></div>
      <div className="nova-transfer-card-controls"><button type="button" className="secondary nova-transfer-toggle"
        aria-expanded={!collapsed} aria-label={collapsed ? '展开转账任务' : '折叠转账任务'}
        onClick={() => setCollapsed(value => !value)}>{collapsed ? '展开' : '折叠'}</button>
      {canCancel && <button type="button" className="icon-button" aria-label="取消这笔转账任务" title="取消这笔任务"
        disabled={disabled || busy} onClick={() => void close()}>×</button>}
      </div>
    </div>
    {!collapsed && <>
    <p className="hint">{visible?.message || (task.missing_fields.length ? `还需：${task.missing_fields.join('、')}` : '请核对转账信息')}</p>
    {task.form.amount_rule && <p className="hint">金额来源：{task.form.amount_rule}{typeof task.form.amount_basis_available_fen === 'number'
      ? `（计算时可用余额 ¥${(task.form.amount_basis_available_fen / 100).toFixed(2)}）` : '；请选择付款账户后计算'}。生成计划后金额固定，确认前请核对。</p>}
    <form onSubmit={event => void save(event)} className="nova-transfer-form">
      <label>收款人姓名<input value={recipient} maxLength={100} disabled={disabled || busy || !canSave}
        onChange={event => setRecipient(event.target.value)} placeholder="请输入收款人姓名" /></label>
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
        {accounts.filter(account => !task.form.amount_rule || ['savings', 'checking'].includes(account.account_type))
          .map(account => <option key={account.account_id} value={account.account_id}>
          {account.account_type} · 尾号 {account.card_number.slice(-4)}
        </option>)}
      </select></label>
      <label>备注（可选）<input value={note} maxLength={256} disabled={disabled || busy || !canSave}
        onChange={event => setNote(event.target.value)} placeholder="转账备注" /></label>
      {canSave && <button type="submit" disabled={disabled || busy || !dirty}>保存卡片信息</button>}
    </form>
    {task.candidates.length > 0 && <div className="nova-transfer-candidates"><p>请选择匹配的账户：</p>
      {task.candidates.map(candidate => <button type="button" key={candidate.candidate_id} className="secondary"
        disabled={disabled || busy} onClick={() => onSelectCandidate(candidate.candidate_id)}>{candidate.label}</button>)}
    </div>}
    {visible?.confirmation_card && <div className="nova-transfer-review">
      <strong>请核对后确认</strong>
      <p>从 {visible.confirmation_card.payer_card_masked || '付款账户'} 转给 {visible.confirmation_card.recipient_name}<br />
        收款账户：{visible.confirmation_card.recipient_card_masked || visible.confirmation_card.recipient_phone_masked || '请核对收款人'}<br />
        金额：¥{visible.confirmation_card.amount.toFixed(2)} · 备注：{visible.confirmation_card.note || '无'}</p>
      <p className="hint">风险级别：{visible.confirmation_card.risk_level}。点击确认后将按银行规则执行；高风险转账还需验证码。</p>
    </div>}
    <div className="nova-transfer-actions">
      {visible?.allowed_actions.includes('confirm') && <button type="button" disabled={disabled || busy || dirty}
        onClick={() => void confirm()}>确认转账</button>}
      {visible?.allowed_actions.includes('reconcile') && <button type="button" disabled={disabled || busy}
        onClick={() => void reconcile()}>核对银行结果</button>}
    </div>
    {visible?.allowed_actions.includes('verify') && <form className="nova-transfer-verify" onSubmit={event => void verify(event)}>
      <strong>身份验证</strong><SecondFactor purpose="transfer" referenceId={visible.task_id} factor={factor} onChange={setFactor} disabled={disabled || busy} />
      <input aria-label="转账验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
        value={code} disabled={disabled || busy} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="6 位验证码" />
      <button type="submit" disabled={disabled || busy || !factor || code.length !== 6}>验证并执行</button>
    </form>}
    {notice && <p className="hint" role="status">{notice}</p>}
    {error && <p className="nova-error" role="alert">{error}</p>}
    </>}
  </article>;
}
