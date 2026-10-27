import { useEffect, useState, type FormEvent } from 'react';
import * as calendar from '../services/calendar';
import { dismissNovaCalendarTask, linkNovaCalendarTask, type NovaChatCalendarTask } from '../services/nova';
import { beijingTime, fromBeijingInput } from '../utils/time';
import NovaCalendarMutationCard from './NovaCalendarMutationCard';

const errorText = (err: unknown) => {
  const detail = (err as { response?: { data?: { detail?: string | { message?: string } } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return err instanceof Error ? err.message : '请求失败，请稍后重试';
};
const shortTime = (value: string) => beijingTime(value).slice(0, 16);

/** NOVA 理解的日程：信息完整时已创建并可删除；不完整时为预填表单，空项由本人补全后创建。 */
export default function NovaCalendarCard(props: {
  task: NovaChatCalendarTask; sessionId: string; disabled: boolean;
  onUpdated: (tasks: NovaChatCalendarTask[]) => void; onActivity?: () => void;
}) {
  if (props.task.kind === 'mutation') return <NovaCalendarMutationCard {...props} />;
  return <LegacyNovaCalendarCard {...props} />;
}

function LegacyNovaCalendarCard({ task, sessionId, disabled, onUpdated, onActivity }: {
  task: NovaChatCalendarTask; sessionId: string; disabled: boolean;
  onUpdated: (tasks: NovaChatCalendarTask[]) => void; onActivity?: () => void;
}) {
  const prefill = task.prefill ?? {};
  const [title, setTitle] = useState(prefill.title ?? '');
  const [day, setDay] = useState(prefill.start_date ?? '');
  const [start, setStart] = useState(prefill.start_time ?? '');
  const [endDay, setEndDay] = useState(prefill.end_date ?? '');
  const [end, setEnd] = useState(prefill.end_time ?? '');
  const [location, setLocation] = useState(prefill.location ?? '');
  const [description, setDescription] = useState(prefill.description ?? '');
  const [event, setEvent] = useState<calendar.CalendarEvent | null>(task.event ?? null);
  const [deleted, setDeleted] = useState(task.deleted);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(task.notice ?? '');

  useEffect(() => { setEvent(task.event ?? null); setDeleted(task.deleted); }, [task.event_id, task.deleted]);

  const off = busy || disabled;
  const missing = [!title.trim() && '事件名称', !day && '日期', !start && '开始时间'].filter(Boolean) as string[];

  async function submit(formEvent: FormEvent) {
    formEvent.preventDefault();
    if (off || missing.length) return;
    // 与日历页一致：表单按北京时间填写。
    const startsAt = fromBeijingInput(`${day}T${start}`);
    const endsAt = end ? fromBeijingInput(`${endDay || day}T${end}`) : null;
    if (endsAt && new Date(endsAt) <= new Date(startsAt)) { setError('结束时间必须晚于开始时间'); return; }
    setBusy(true); setError('');
    try {
      const created = await calendar.createEvent({ title: title.trim(), description, location, starts_at: startsAt, ends_at: endsAt });
      onUpdated(await linkNovaCalendarTask(sessionId, task.entry_id, created.event_id));
      setEvent(created); onActivity?.();
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (off || !event) return;
    setBusy(true); setError('');
    try { await calendar.deleteEvent(event.event_id); setDeleted(true); onActivity?.(); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }

  async function dismiss() {
    if (off) return;
    setBusy(true); setError('');
    try { onUpdated(await dismissNovaCalendarTask(sessionId, task.entry_id)); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }

  return <article className="card nova-transfer-card nova-calendar-card" aria-label="日程">
    <div className="nova-transfer-card-head"><div>
      <strong>{event ? `日程 · ${event.title}` : '新建日程'}</strong>
      <small>{deleted ? '已删除' : event ? '已添加到本人日历' : missing.length ? `还需填写：${missing.join('、')}` : '信息已齐全，核对后创建'}</small>
    </div></div>

    {event ? <>
      <ul className="history">
        <li><span>时间（北京时间）</span><span>{shortTime(event.starts_at)}{event.ends_at ? ` – ${shortTime(event.ends_at)}` : ''}</span></li>
        {event.location && <li><span>地点</span><span>{event.location}</span></li>}
        {event.description && <li><span>备注</span><span>{event.description}</span></li>}
      </ul>
      {!deleted && <div className="nova-transfer-actions"><button type="button" className="secondary" disabled={off} onClick={() => void remove()}>{busy ? '正在删除…' : '删除此日程'}</button></div>}
    </> : <form className="nova-collection-form" onSubmit={event => void submit(event)}>
      <label>事件名称<input required maxLength={200} value={title} disabled={off} placeholder="例如：和朋友吃饭" onChange={e => setTitle(e.target.value)} /></label>
      <label>日期<input type="date" required value={day} disabled={off} onChange={e => setDay(e.target.value)} /></label>
      <label>开始时间（北京时间）<input type="time" required value={start} disabled={off} onChange={e => setStart(e.target.value)} /></label>
      <label>结束时间（可选）<input type="time" value={end} disabled={off} onChange={e => setEnd(e.target.value)} /></label>
      {end && <label>结束日期（跨天时填写）<input type="date" value={endDay} min={day} disabled={off} onChange={e => setEndDay(e.target.value)} /></label>}
      <label>地点（可选）<input maxLength={500} value={location} disabled={off} onChange={e => setLocation(e.target.value)} /></label>
      <label>备注（可选）<textarea rows={2} maxLength={10000} value={description} disabled={off} onChange={e => setDescription(e.target.value)} /></label>
      <div className="nova-transfer-actions">
        <button type="button" className="secondary" disabled={off} onClick={() => void dismiss()}>不创建</button>
        <button type="submit" disabled={off || missing.length > 0}>{busy ? '正在创建…' : '创建日程'}</button>
      </div>
    </form>}
    {error && <p className={event ? 'error' : 'hint'} role="alert">{error}</p>}
  </article>;
}
