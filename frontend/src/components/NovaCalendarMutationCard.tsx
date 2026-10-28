import { useEffect, useState, type FormEvent } from 'react';
import { confirmNovaCalendarTask, dismissNovaCalendarTask, type NovaChatCalendarTask } from '../services/nova';

const labels: Record<string, string> = {
  title: '事件名称', start_date: '开始日期', start_time: '开始时间',
  end_date: '结束日期', end_time: '结束时间', location: '地点', description: '备注',
};
const actions = { add: '添加', modify: '修改', delete: '删除' };
const errorText = (error: unknown) => {
  const detail = (error as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : detail?.message || (error instanceof Error ? error.message : '操作失败');
};

export default function NovaCalendarMutationCard({ task, sessionId, disabled, onUpdated, onActivity }: {
  task: NovaChatCalendarTask; sessionId: string; disabled: boolean;
  onUpdated: (tasks: NovaChatCalendarTask[]) => void; onActivity?: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(task.changes ?? {});
  useEffect(() => setValues(task.changes ?? {}), [task.changes]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const action = task.action ?? 'add';
  const done = task.status === 'COMPLETED';
  const targets = task.targets ?? [];
  const missing = action === 'add' && (!values.title?.trim() || !values.start_date || !values.start_time);
  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (busy || disabled || done || missing) return;
    setBusy(true); setError('');
    try {
      onUpdated(await confirmNovaCalendarTask(sessionId, task.entry_id, action === 'delete' ? {} : values));
      onActivity?.();
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  async function dismiss() {
    if (busy || disabled || done) return;
    setBusy(true); setError('');
    try { onUpdated(await dismissNovaCalendarTask(sessionId, task.entry_id)); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  const edit = (key: string, value: string) => setValues(previous => ({ ...previous, [key]: value }));
  return <article className="card nova-transfer-card nova-calendar-card" aria-label="日程操作">
    <div className="nova-transfer-card-head"><div>
      <strong>{actions[action]}日程</strong>
      <small>{done ? `已${actions[action]} ${task.result?.length ?? targets.length} 项` : '待你核对并确认执行'}</small>
    </div></div>
    {action !== 'add' && <>
      <p className="hint">本次将{actions[action]}以下 {targets.length} 项{targets.some(item => item.recurring) ? '；重复日程会处理整个系列' : ''}：</p>
      <ul className="history">{targets.map(item => <li key={item.event_id}>
        <span>{item.title}</span><span>{new Date(item.starts_at_local).toLocaleString('zh-CN')} {item.recurring ? '（整个系列）' : ''}</span>
      </li>)}</ul>
    </>}
    {!done && <form className="nova-collection-form" onSubmit={event => void confirm(event)}>
      {action !== 'delete' && Object.entries(labels).filter(([key]) => action === 'add' || key in values).map(([key, label]) =>
        <label key={key}>{label}
          {key === 'description' ? <textarea rows={2} maxLength={2000} value={values[key] ?? ''} disabled={busy || disabled} onChange={event => edit(key, event.target.value)} /> :
            <input type={key.endsWith('_date') ? 'date' : key.endsWith('_time') ? 'time' : 'text'}
              required={action === 'add' && ['title', 'start_date', 'start_time'].includes(key)}
              maxLength={key === 'title' ? 200 : key === 'location' ? 500 : undefined}
              value={values[key] ?? ''} disabled={busy || disabled} onChange={event => edit(key, event.target.value)} />}
        </label>)}
      <div className="nova-transfer-actions">
        <button type="button" className="secondary" disabled={busy || disabled} onClick={() => void dismiss()}>取消任务</button>
        <button type="submit" disabled={busy || disabled || Boolean(missing)}>{busy ? '正在处理…' : `确认${actions[action]}${targets.length ? ` ${targets.length} 项` : ''}`}</button>
      </div>
    </form>}
    {error && <p className="error" role="alert">{error}</p>}
  </article>;
}
