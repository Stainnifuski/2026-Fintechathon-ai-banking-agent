import { useEffect, useRef, useState } from 'react';
import { auditEvents, auditObject, auditTrace, exportAudit } from '../../services/audit';
import type { AuditEvent, AuditFilters, AuditPage } from '../../services/audit';
import { beijingTime } from '../../utils/time';
import { actionLabel, domains, evidenceSummary, outcomes, permissionLabel, phases } from './view';
import './styles.css';

const day = (offset: number) => beijingTime(Date.now() + offset * 86400000).slice(0, 10);
const errorMessage = (error: unknown) => {
  const detail = (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail;
  return detail?.message || '操作记录暂时无法加载，请稍后重试。';
};
type Inputs = { since: string; until: string; domain: string; outcome: string; permission: string; objectType: string; objectId: string; all: boolean };
function buildFilters(input: Inputs): AuditFilters {
  if (input.since > input.until) throw new Error('开始日期不能晚于结束日期');
  return {
    since: new Date(`${input.since}T00:00:00+08:00`).toISOString(),
    until: new Date(`${input.until}T23:59:59.999+08:00`).toISOString(),
    domain: input.domain || undefined, outcome: input.outcome || undefined,
    permission_level: input.permission || undefined,
    object_type: input.objectId.trim() ? input.objectType : undefined,
    object_id: input.objectId.trim() || undefined, view: input.all ? 'all' : 'business',
  };
}

export default function Audit() {
  const [inputs, setInputs] = useState<Inputs>({ since: day(-6), until: day(0), domain: '', outcome: '', permission: '', objectType: 'task', objectId: '', all: false });
  const [applied, setApplied] = useState(() => buildFilters(inputs));
  const [page, setPage] = useState<AuditPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [detail, setDetail] = useState<AuditPage | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [exporting, setExporting] = useState(false);
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const change = <K extends keyof Inputs>(key: K, value: Inputs[K]) => setInputs(old => ({ ...old, [key]: value }));

  useEffect(() => {
    let active = true;
    const run = ++generation.current;
    setLoading(true); setError(''); setPage(null); setSelected(null); setDetail(null);
    detailGeneration.current++;
    auditEvents(applied).then(data => { if (active && run === generation.current) setPage(data); })
      .catch(err => { if (active) setError(errorMessage(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; generation.current++; detailGeneration.current++; };
  }, [applied]);

  async function more() {
    if (!page?.next_cursor || loading) return;
    const run = generation.current;
    setLoading(true); setError('');
    try {
      const next = await auditEvents({ ...applied, cursor: page.next_cursor });
      if (run === generation.current) setPage(old => ({ ...next, items: [...(old?.items || []), ...next.items] }));
    } catch (err) { if (run === generation.current) setError(errorMessage(err)); }
    finally { if (run === generation.current) setLoading(false); }
  }
  async function show(event: AuditEvent, objectIndex?: number) {
    const run = ++detailGeneration.current;
    setSelected(event); setDetail(null); setDetailError(''); setDetailBusy(true);
    try {
      const data = objectIndex !== undefined ? await auditObject(event.objects[objectIndex]) :
        event.trace_id ? await auditTrace(event.trace_id) : { items: [event], audit_health: page!.audit_health };
      if (run === detailGeneration.current) setDetail(data);
    } catch (err) { if (run === detailGeneration.current) setDetailError(errorMessage(err)); }
    finally { if (run === detailGeneration.current) setDetailBusy(false); }
  }
  async function download() {
    setExporting(true); setError('');
    try {
      const blob = await exportAudit(applied);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = '操作审计记录.json'; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setError('导出未完成。每次最多31天、5000条，请缩小范围后重试。'); }
    finally { setExporting(false); }
  }
  return <div className="operation-audit">
    <div className="page-intro"><p className="eyebrow">ACTIVITY</p><h1>操作记录</h1><p>查看本人操作的决策依据、确认验证和实际执行结果。</p></div>
    {page?.audit_health.status !== undefined && page.audit_health.status !== 'ok' && <p className="notice" role="status">操作记录正在恢复保存，部分最新记录可能稍后显示。已提交交易请以银行结果为准。</p>}
    <form className="card audit-filters" onSubmit={e => {
      e.preventDefault(); try { setApplied(buildFilters(inputs)); } catch (err) { setError((err as Error).message); }
    }}>
      <label>开始日期（北京时间）<input type="date" required value={inputs.since} onChange={e => change('since', e.target.value)} /></label>
      <label>结束日期<input type="date" required value={inputs.until} onChange={e => change('until', e.target.value)} /></label>
      <label>业务<select value={inputs.domain} onChange={e => change('domain', e.target.value)}><option value="">全部业务</option>{Object.entries(domains).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>结果<select value={inputs.outcome} onChange={e => change('outcome', e.target.value)}><option value="">全部结果</option>{Object.entries(outcomes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>授权要求<select value={inputs.permission} onChange={e => change('permission', e.target.value)}><option value="">全部要求</option>{['green', 'yellow', 'red', 'delegated', 'unknown'].map(key => <option key={key} value={key}>{permissionLabel(key)}</option>)}</select></label>
      <label>关联对象<select value={inputs.objectType} onChange={e => change('objectType', e.target.value)}>{[['task', '任务'], ['operation', '操作'], ['plan', '计划'], ['transaction', '交易流水'], ['session', '对话'], ['card', '卡片'], ['order', '订单']].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label className="audit-object-input">对象编号<input value={inputs.objectId} maxLength={128} onChange={e => change('objectId', e.target.value)} placeholder="可选，粘贴任务或交易编号" /></label>
      <label className="audit-checkbox"><input type="checkbox" checked={inputs.all} onChange={e => change('all', e.target.checked)} />显示请求与连接步骤</label>
      <div className="audit-buttons"><button type="submit" disabled={loading}>查询记录</button><button type="button" className="secondary" disabled={exporting || loading} onClick={() => void download()}>{exporting ? '正在导出…' : '导出当前范围'}</button></div>
    </form>
    {error && <p className="notice" role="alert">{error}</p>}
    <section className="card audit-list" aria-busy={loading}>
      <div className="audit-heading"><h2>操作与处理步骤</h2><span className="hint">时间均为北京时间 · 仅本人记录</span></div>
      {loading && !page && <p role="status">正在加载操作记录…</p>}
      {page && !page.items.length && <p className="audit-empty">这个范围内还没有操作记录。可调整日期或业务筛选后查询。</p>}
      {!!page?.items.length && <div className="audit-table-wrap"><table><thead><tr><th>时间</th><th>操作</th><th>业务</th><th>结果</th><th>授权要求</th><th>操作者</th><th>详情</th></tr></thead><tbody>{page.items.map(event => <tr key={event.event_id} className={selected?.event_id === event.event_id ? 'selected' : ''}>
        <td>{beijingTime(event.occurred_at)}</td><td><strong>{actionLabel(event)}</strong><small>{phases[event.phase] || event.phase}{event.legacy ? ' · 历史资料不完整' : ''}</small></td>
        <td>{domains[event.domain] || event.domain}</td><td><span className={`audit-outcome ${event.outcome}`}>{outcomes[event.outcome] || event.outcome}</span></td><td>{permissionLabel(event.permission_level)}</td>
        <td>{event.actor_type === 'user' ? event.actor_id === event.subject_user_id ? '本人' : '其他参与方' : event.actor_type === 'unknown' ? '历史未记录' : '系统处理'}</td><td><button className="secondary" onClick={() => void show(event)} aria-label={`查看${actionLabel(event)}的链路`}>查看链路</button></td>
      </tr>)}</tbody></table></div>}
      {page?.next_cursor && <button className="secondary audit-more" disabled={loading} onClick={() => void more()}>{loading ? '正在加载…' : '加载更多'}</button>}
    </section>
    {selected && <section className="card audit-detail" aria-busy={detailBusy}>
      <div className="audit-heading"><div><h2>决策与执行链路</h2><p className="hint">确认、验证与银行结果分别留痕；请求完成后也可能仍需本人操作或核对。</p></div><button className="secondary" onClick={() => { detailGeneration.current++; setSelected(null); }}>收起</button></div>
      {!!selected.objects.length && <div className="audit-related"><span>查看完整生命周期：</span>{selected.objects.slice(0, 8).map((obj, index) => <button className="secondary" key={`${obj.domain}:${obj.object_type}:${obj.object_id}`} onClick={() => void show(selected, index)} title={obj.object_id}>{({ task: '关联任务', operation: '关联操作', plan: '关联计划', authorization: '关联授权', transaction: '关联流水', session: '关联对话' } as Record<string, string>)[obj.object_type] || '关联对象'}</button>)}</div>}
      {detailBusy && <p role="status">正在还原处理链路…</p>}{detailError && <p role="alert">{detailError}</p>}
      {(detail?.truncated || detail?.next_cursor) && <p className="notice">本次显示部分步骤。请按关联对象缩小范围，或按日期分段导出完整记录。</p>}
      <ol className="audit-timeline">{detail?.items.map(event => <li key={event.event_id}>
        <div className="audit-step-heading"><strong>{actionLabel(event)}</strong><span className={`audit-outcome ${event.outcome}`}>{outcomes[event.outcome] || event.outcome}</span></div>
        <p className="hint">{beijingTime(event.occurred_at)} · {phases[event.phase] || event.phase}{event.duration_ms != null ? ` · ${event.duration_ms} 毫秒` : ''}</p>
        {evidenceSummary(event).map((text, index) => <p key={index}>{text}</p>)}
        {event.legacy && <p className="notice">此记录产生于统一审计启用前，部分决策和验证步骤未留存。</p>}
        <details><summary>查看留存依据与编号</summary><p>记录编号：{event.event_id}</p>{event.objects.map(obj => <p key={`${obj.domain}:${obj.object_type}:${obj.object_id}`}>{obj.object_type}：{obj.object_id}{obj.version ? ` · 版本 ${obj.version}` : ''}</p>)}<pre>{JSON.stringify(event.facts, null, 2)}</pre></details>
      </li>)}</ol>
    </section>}
  </div>;
}
