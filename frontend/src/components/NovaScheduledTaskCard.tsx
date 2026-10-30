import {requestFeedback} from '../utils/requestFeedback';
import { useEffect, useState, type FormEvent } from 'react';
import * as bank from '../services/bank';
import { dismissNovaScheduledTask, linkNovaScheduledTask, type NovaChatScheduledTask } from '../services/nova';
import { beijingTime, fromBeijingInput } from '../utils/time';

const STATUS: Record<string, string> = {
  SCHEDULED: '等待执行时间', READY: '可以开始', STARTED: '已开始', RUNNING: '进行中', FINISHED: '已完成', CANCELLED: '已取消',
};
const yuan = (fen: number) => `¥${(fen / 100).toFixed(2)}`;
const errorText=requestFeedback;

/** NOVA 理解的自定义定时任务：完整时已创建；否则为预填表单。开始、完成、取消均由本人点击。 */
export default function NovaScheduledTaskCard({ task, sessionId, accounts, disabled, onUpdated, onStart, onActivity }: {
  task: NovaChatScheduledTask; sessionId: string; accounts: bank.Account[]; disabled: boolean;
  onUpdated: (tasks: NovaChatScheduledTask[]) => void; onStart: (task: bank.ScheduledTask) => void; onActivity?: () => void;
}) {
  const prefill = task.prefill;
  const lockable = accounts.filter(a => ['savings', 'checking'].includes(a.account_type));
  const [description, setDescription] = useState(prefill.description ?? '');
  const [day, setDay] = useState(prefill.run_date ?? '');
  const [time, setTime] = useState(prefill.run_time ?? '');
  const [lockAmount, setLockAmount] = useState(prefill.lock_amount ?? '');
  const [lockAccount, setLockAccount] = useState(prefill.lock_account_id ?? '');
  const [view, setView] = useState<bank.ScheduledTask | null>(task.task ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(task.notice ?? '');
  const accountId = lockAccount || lockable[0]?.account_id || '';

  useEffect(() => { setView(task.task ?? null); }, [task.task?.task_id, task.task?.status]);

  const off = busy || disabled;
  const missing = [!description.trim() && '任务内容', !day && '执行日期', !time && '执行时间'].filter(Boolean) as string[];
  const act = async (step: () => Promise<void>) => {
    if (off) return;
    setBusy(true); setError('');
    try { await step(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };

  const save = (event: FormEvent) => {
    event.preventDefault();
    void act(async () => {
      if (missing.length) return;
      const instant = fromBeijingInput(`${day}T${time}`);
      if (new Date(instant).getTime() <= Date.now()) throw new Error('执行时间必须晚于当前时间');
      const lock = lockAmount.trim() ? { amount: lockAmount.trim(), account_id: accountId } : undefined;
      const created = await bank.createScheduledTask(description.trim(), instant, lock);
      onUpdated(await linkNovaScheduledTask(sessionId, task.entry_id, created.task_id));
      setView(created);
      if (lock) onActivity?.();
    });
  };
  const start = () => act(async () => {
    const started = await bank.startScheduledTask(view!.task_id);
    setView(started); onStart(started);
  });
  const end = (finished: boolean) => act(async () => {
    setView(finished ? await bank.finishScheduledTask(view!.task_id) : await bank.cancelScheduledTask(view!.task_id));
    onActivity?.();
  });
  const dismiss = () => act(async () => { onUpdated(await dismissNovaScheduledTask(sessionId, task.entry_id)); });

  const lock = view?.lock;
  return <article className="card nova-transfer-card nova-scheduled-card" aria-label="定时任务">
    <div className="nova-transfer-card-head"><div>
      <strong>{view ? '定时任务' : '新建定时任务'}</strong>
      <small>{view ? STATUS[view.status] ?? view.status : missing.length ? `还需填写：${missing.join('、')}` : '信息已齐全，核对后保存'}</small>
    </div></div>

    {view ? <>
      <p>{view.description}</p>
      <ul className="history">
        <li><span>执行时间（北京时间）</span><span>{beijingTime(view.scheduled_at).slice(0, 16)}</span></li>
        {lock && <li><span>锁定资金</span><span>{lock.status === 'HELD'
          ? `${yuan(lock.amount_fen)}${lock.used_fen ? ` · 已用 ${yuan(lock.used_fen)} · 剩余 ${yuan(lock.remaining_fen)}` : ''}`
          : `${yuan(lock.amount_fen)} · 已用 ${yuan(lock.used_fen)} · 已退回 ${yuan(lock.returned_fen)}`}</span></li>}
      </ul>
      {lock?.status === 'HELD' && <p className="hint">锁定的资金在任务结束前不能被其他付款使用；开始后只有本任务在对话中的付款可以使用，完成或取消后剩余部分退回活期。</p>}
      {view.status === 'SCHEDULED' && <p className="hint">到执行时间后，可在这里点击“开始执行”，NOVA 才会按任务内容办理。</p>}
      <div className="nova-transfer-actions">
        {view.can_start && <button type="button" disabled={off} onClick={() => void start()}>开始执行</button>}
        {view.can_finish && <button type="button" disabled={off} onClick={() => void end(true)}>完成任务</button>}
        {view.can_cancel && <button type="button" className="secondary" disabled={off} onClick={() => void end(false)}>{lock ? '取消任务并退回资金' : '取消任务'}</button>}
      </div>
    </> : <form className="nova-collection-form" onSubmit={save}>
      <label>任务内容<textarea rows={2} maxLength={2000} value={description} disabled={off} placeholder="例如：查询本月支出并帮我制定预算" onChange={e => setDescription(e.target.value)} /></label>
      <label>执行日期<input type="date" value={day} disabled={off} onChange={e => setDay(e.target.value)} /></label>
      <label>执行时间（北京时间）<input type="time" value={time} disabled={off} onChange={e => setTime(e.target.value)} /></label>
      <label>锁定活期资金（元，可选）<input type="number" min="0.01" step="0.01" value={lockAmount} disabled={off} placeholder="不锁定可留空" onChange={e => setLockAmount(e.target.value)} /></label>
      {lockAmount.trim() && <label>从账户<select value={accountId} disabled={off} onChange={e => setLockAccount(e.target.value)}>
        {lockable.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}{typeof a.available_fen === 'number' ? ` · 可用 ${yuan(a.available_fen)}` : ''}</option>)}</select></label>}
      <p className="hint">保存时立即锁定资金；可自由支配余额不足则保存失败。</p>
      <div className="nova-transfer-actions">
        <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>不创建</button>
        <button type="submit" disabled={off || missing.length > 0}>{busy ? '正在保存…' : '保存定时任务'}</button>
      </div>
    </form>}
    {error && <p className={view ? 'error' : 'hint'} role="alert">{error}</p>}
  </article>;
}
