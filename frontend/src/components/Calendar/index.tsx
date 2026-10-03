import { useEffect, useMemo, useRef, useState } from 'react';
import * as calendar from '../../services/calendar';
import './styles.css';

const pad = (n: number) => String(n).padStart(2, '0');
const dateKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const dayDate = (key: string) => new Date(`${key}T00:00:00`);
const timeText = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
const eventKey = (event: calendar.CalendarEvent) => `${event.event_id}-${event.starts_at}`;
const eventDate = (event: calendar.CalendarEvent) => event.all_day ? event.starts_at.slice(0, 10) : dateKey(new Date(event.starts_at));

function onDay(event: calendar.CalendarEvent, day: Date) {
  const key = dateKey(day);
  if (event.all_day) return event.starts_at.slice(0, 10) <= key && key < (event.ends_at?.slice(0, 10) || event.starts_at.slice(0, 10));
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
  return new Date(event.starts_at) < next && (event.ends_at ? new Date(event.ends_at) > day : eventDate(event) === key);
}

function describeTime(event: calendar.CalendarEvent) {
  if (event.all_day) return '全天';
  const start = timeText(event.starts_at);
  if (!event.ends_at) return `${start} · 时间点`;
  const end = new Date(event.ends_at);
  const endDay = dateKey(end);
  return `${start} – ${eventDate(event) === endDay ? '' : `${endDay} `}${timeText(event.ends_at)}`;
}

function errorMessage(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: string | { message?: string } | { msg?: string }[] } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail[0]?.msg || '请检查事件信息';
  if (detail?.message) return detail.message;
  return error instanceof Error ? error.message : '日历暂时不可用，请稍后重试';
}

export default function Calendar({ revision }: { revision: number }) {
  const today = dateKey(new Date());
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const [selected, setSelected] = useState(today);
  const [month, setMonth] = useState(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [events, setEvents] = useState<calendar.CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [loadError, setLoadError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState<'point' | 'range'>('point');
  const [starts, setStarts] = useState('');
  const [ends, setEnds] = useState('');
  const [description, setDescription] = useState('');
  const [location, setLocation] = useState('');
  const [formError, setFormError] = useState('');
  const [deleting, setDeleting] = useState<calendar.CalendarEvent | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const days = useMemo(() => {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const offset = (first.getDay() + 6) % 7;
    return Array.from({ length: 42 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), 1 - offset + i));
  }, [month]);
  const start = days[0].toISOString();
  const end = new Date(days[41].getFullYear(), days[41].getMonth(), days[41].getDate() + 1).toISOString();

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError('');
    calendar.listEvents(start, end, timeZone).then(items => {
      if (active) setEvents(items);
    }).catch(error => {
      if (active) { setEvents([]); setLoadError(errorMessage(error)); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [start, end, timeZone, revision, refresh]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { setOpen(false); setDeleting(null); }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [busy]);

  const selectedEvents = events.filter(event => onDay(event, dayDate(selected)));
  const selectDay = (key: string) => {
    setSelected(key);
    const d = dayDate(key);
    setMonth(current => current.getFullYear() === d.getFullYear() && current.getMonth() === d.getMonth() ? current : new Date(d.getFullYear(), d.getMonth(), 1));
  };
  const moveMonth = (offset: number) => {
    const next = new Date(month.getFullYear(), month.getMonth() + offset, 1);
    setMonth(next);
    setSelected(dateKey(next));
  };
  const add = () => {
    setTitle(''); setKind('point'); setStarts(`${selected}T09:00`); setEnds(`${selected}T10:00`);
    setDescription(''); setLocation(''); setFormError(''); setOpen(true);
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const startDate = new Date(starts);
    const endDate = kind === 'range' ? new Date(ends) : null;
    if (!title.trim()) { setFormError('请输入事件名称'); return; }
    if (Number.isNaN(startDate.getTime()) || (endDate && (Number.isNaN(endDate.getTime()) || endDate <= startDate))) {
      setFormError('结束时间必须晚于开始时间'); return;
    }
    const matchesInput = (date: Date, input: string) => `${dateKey(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}` === input;
    if (!matchesInput(startDate, starts) || (endDate && !matchesInput(endDate, ends))) {
      setFormError('所选时间在当前时区不存在，请重新选择时间'); return;
    }
    setBusy(true); setFormError('');
    try {
      await calendar.createEvent({ title: title.trim(), description, location, starts_at: startDate.toISOString(), ends_at: endDate?.toISOString() ?? null });
      setOpen(false); selectDay(dateKey(startDate)); setRefresh(value => value + 1); setNotice('事件已添加');
    } catch (error) { setFormError(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const upload = async (file?: File) => {
    if (!file || busy) return;
    if (!file.name.toLowerCase().endsWith('.ics') || file.size === 0 || file.size > 2 * 1024 * 1024) {
      setNotice('请选择非空且不超过 2 MB 的 .ics 文件'); return;
    }
    setBusy(true); setNotice('');
    try {
      const result = await calendar.importCalendar(file, timeZone);
      setNotice(`导入完成：新增 ${result.created} 项，更新 ${result.updated} 项，跳过 ${result.skipped} 项相同日程。可切换日期查看。`);
      setRefresh(value => value + 1);
    } catch (error) { setNotice(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!deleting || busy) return;
    setBusy(true);
    try {
      await calendar.deleteEvent(deleting.event_id);
      setDeleting(null); setRefresh(value => value + 1); setNotice('事件已删除');
    } catch (error) { setNotice(errorMessage(error)); }
    finally { setBusy(false); }
  };

  return <section className="calendar-module">
    <div className="calendar-hero card">
      <div><p className="eyebrow">我的日历</p><h2>为重要的事，留一点时间</h2><p>记录一个时间点，或安排一段时光。也可以导入已有日历。</p></div>
      <div className="calendar-actions">
        <button className="secondary" disabled={busy} onClick={() => fileRef.current?.click()}>{busy && !open && !deleting ? '正在导入…' : '导入 .ics'}</button>
        <button disabled={busy} onClick={add}>＋ 添加事件</button>
        <input ref={fileRef} type="file" accept=".ics,text/calendar" hidden aria-label="上传日历文件" onChange={event => {
          const file = event.target.files?.[0]; event.target.value = ''; void upload(file);
        }} />
      </div>
    </div>
    {notice && <p className="notice" role="status">{notice}</p>}
    {loadError && <div className="calendar-error" role="alert"><span>{loadError}</span><button className="secondary" onClick={() => setRefresh(value => value + 1)}>重试</button></div>}
    <div className="calendar-layout">
      <section className="card calendar-month" aria-label="月历" aria-busy={loading}>
        <div className="calendar-toolbar">
          <h2>{month.getFullYear()} 年 {month.getMonth() + 1} 月</h2>
          <div className="calendar-actions"><button className="secondary" aria-label="上个月" onClick={() => moveMonth(-1)}>‹</button><button className="secondary" onClick={() => selectDay(today)}>今天</button><button className="secondary" aria-label="下个月" onClick={() => moveMonth(1)}>›</button></div>
        </div>
        <div className="calendar-weekdays">{['一', '二', '三', '四', '五', '六', '日'].map(d => <span key={d}>周{d}</span>)}</div>
        <div className="calendar-days">
          {days.map(day => {
            const key = dateKey(day);
            const items = loading ? [] : events.filter(event => onDay(event, day));
            return <button key={key} aria-label={`${key}，${items.length} 个事件`} aria-pressed={selected === key} className={`calendar-day ${day.getMonth() !== month.getMonth() ? 'outside' : ''} ${selected === key ? 'selected' : ''} ${key === today ? 'today' : ''}`} onClick={() => selectDay(key)}>
              <span className="calendar-day-number">{day.getDate()}</span>
              {items.slice(0, 2).map(item => <span className={`calendar-chip ${item.source}`} key={eventKey(item)}>{item.all_day ? '全天' : timeText(item.starts_at)} {item.title}</span>)}
              {items.length > 2 && <small>还有 {items.length - 2} 项</small>}
              {items.length > 0 && <span className="calendar-mobile-dot" />}
            </button>;
          })}
        </div>
        <p className="hint calendar-footnote">显示时区：{timeZone} · 导入的全天事件按原日期显示</p>
      </section>
      <aside className="card calendar-agenda">
        <div className="calendar-agenda-heading"><p className="eyebrow">当日安排</p><h2>{dayDate(selected).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })}</h2></div>
        <label>选择日期<input aria-label="选择日历日期" type="date" value={selected} onChange={event => { if (event.target.value) selectDay(event.target.value); }} /></label>
        {loading ? <p className="hint" role="status">正在加载日程…</p> : loadError ? <p className="hint">日程未能加载，请重试。</p> : selectedEvents.length === 0 ? <div className="calendar-empty"><strong>今天还有空白</strong><p>给计划、约会或纪念日留一个位置。</p><button className="secondary" disabled={busy} onClick={add}>添加当天事件</button></div> :
          <ul className="calendar-event-list">{selectedEvents.map(item => <li key={eventKey(item)}>
            <div className="calendar-event-meta"><span>{describeTime(item)}</span><small>{item.source === 'ics' ? '导入' : '手动'}{item.recurring ? ' · 重复' : ''}</small></div>
            <h3>{item.title}</h3>
            {eventDate(item) !== selected && <small>开始于 {eventDate(item)}</small>}
            {item.location && <p className="calendar-location">地点：{item.location}</p>}
            {item.description && <p className="calendar-description">{item.description}</p>}
            <button className="calendar-delete" disabled={busy} onClick={() => setDeleting(item)}>删除{item.recurring ? '整个重复日程' : '事件'}</button>
          </li>)}</ul>}
      </aside>
    </div>
    {open && <div className="calendar-modal" role="dialog" aria-modal="true" aria-labelledby="calendar-form-title">
      <section className="card"><div className="calendar-toolbar"><h2 id="calendar-form-title">添加事件</h2><button className="secondary" disabled={busy} onClick={() => setOpen(false)} aria-label="关闭添加事件">×</button></div>
        <form onSubmit={save}>
          <label>事件名称<input autoFocus required maxLength={200} value={title} placeholder="例如：生日、取蛋糕、朋友聚餐" onChange={e => setTitle(e.target.value)} /></label>
          <fieldset className="calendar-kind"><legend>时间安排</legend><label><input type="radio" name="event-kind" checked={kind === 'point'} onChange={() => setKind('point')} />时间点</label><label><input type="radio" name="event-kind" checked={kind === 'range'} onChange={() => setKind('range')} />时间段</label></fieldset>
          <label>{kind === 'point' ? '事件时间' : '开始时间'}<input type="datetime-local" required value={starts} onChange={e => setStarts(e.target.value)} /></label>
          {kind === 'range' && <label>结束时间<input type="datetime-local" required min={starts} value={ends} onChange={e => setEnds(e.target.value)} /></label>}
          <label>地点（可选）<input maxLength={500} value={location} onChange={e => setLocation(e.target.value)} /></label>
          <label>备注（可选）<textarea rows={3} maxLength={10000} value={description} onChange={e => setDescription(e.target.value)} /></label>
          <p className="hint">按 {timeZone} 保存。时间段可以跨越不同日期。</p>
          {formError && <p className="error" role="alert">{formError}</p>}
          <div className="calendar-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setOpen(false)}>取消</button><button disabled={busy} type="submit">{busy ? '正在保存…' : '保存事件'}</button></div>
        </form>
      </section>
    </div>}
    {deleting && <div className="calendar-modal" role="dialog" aria-modal="true" aria-labelledby="calendar-delete-title"><section className="card"><h2 id="calendar-delete-title">删除{deleting.recurring ? '整个重复日程' : '事件'}？</h2><p>{deleting.title}</p>{deleting.recurring && <p className="hint">此操作会删除该日程的所有日期安排。</p>}<div className="calendar-actions"><button className="secondary" disabled={busy} onClick={() => setDeleting(null)}>取消</button><button disabled={busy} onClick={remove}>{busy ? '正在删除…' : '确认删除'}</button></div></section></div>}
  </section>;
}
