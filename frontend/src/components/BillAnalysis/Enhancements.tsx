import { FormEvent, useEffect, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import * as bills from '../../services/bills';

const money = (fen: number) => (fen / 100).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' });
const labels: Record<string, string> = { dining: '餐饮', shopping: '购物', transport: '交通', entertainment: '娱乐', subscription: '订阅', other: '其他' };
const time = (s: string) => new Date(s).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
const errorText = (e: unknown) => (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || '读取失败，请重试。';

function Evidence({ current, previous, onSelect, selectedId }: { current: bills.Evidence[]; previous: bills.Evidence[]; onSelect: (id: string) => void; selectedId: string | null }) {
  return <details className="insight-evidence"><summary>查看本期与上期流水依据</summary>{([['本期', current], ['上期', previous]] as [string, bills.Evidence[]][]).map(([label, rows]) => <div key={label}><strong>{label}</strong>{rows.length === 0 ? <p className="hint">当前对比范围没有对应流水</p> : rows.map(tx => <div className="insight-evidence-row" key={tx.tx_id}><span>{time(tx.timestamp)} · {money(tx.amount_fen)}<small>{tx.tx_id} · {tx.account_id}</small></span><button className="secondary" aria-pressed={selectedId === tx.tx_id} onClick={() => onSelect(tx.tx_id)}>{selectedId === tx.tx_id ? '已选中 · 可问 NOVA' : '选中供 NOVA 解释'}</button></div>)}</div>)}</details>;
}

export default function Enhancements({ result, revision, onSelect, onManage, selectedId }: {
  result: bills.BillAnalysis; revision: number; selectedId: string | null; onSelect: (id: string) => void; onManage?: (id: string) => void;
}) {
  const [subscriptions, setSubscriptions] = useState<bills.SubscriptionInsights | null>(null);
  const [subError, setSubError] = useState('');
  const [retry, setRetry] = useState(0);
  const [subBusy, setSubBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setSubscriptions(null); setSubError(''); setSubBusy(true);
    bills.subscriptionInsights(result.query).then(data => { if (!cancelled) setSubscriptions(data); })
      .catch(e => { if (!cancelled) setSubError(errorText(e)); })
      .finally(() => { if (!cancelled) setSubBusy(false); });
    return () => { cancelled = true; };
  }, [result, revision, retry]);
  const explanation = result.change_explanation;
  return <>
    <section className="bill-enhancement" aria-labelledby="bill-drivers"><h3 id="bill-drivers">消费变化追溯</h3>
      <p>总差额 <strong>{money(explanation.total_change_fen)}</strong>：次数变化 {money(explanation.frequency_effect_fen)} · 平均单笔金额变化 {money(explanation.unit_amount_effect_fen)} · 本期出现／未出现 {money(explanation.entry_exit_effect_fen)}</p>
      {!explanation.drivers.length && <p className="hint">同商户同分类金额没有变化，暂无差额需要追溯。</p>}
      {explanation.drivers.map((driver, i) => <details className="bill-driver" key={`${driver.category}-${driver.merchant}`} open={i === 0}>
        <summary>{driver.merchant} · {labels[driver.category]} <strong>{money(driver.change_fen)}</strong>{driver.new_large_single && <span className="bill-badge">本期新出现单笔大额</span>}</summary>
        <p>本期 {money(driver.current_fen)}／{driver.current_count} 笔；上期 {money(driver.previous_fen)}／{driver.previous_count} 笔。</p><p>{driver.explanation}</p>
        <Evidence current={driver.current_evidence} previous={driver.previous_evidence} onSelect={onSelect} selectedId={selectedId} />
      </details>)}
      <details className="bill-notices"><summary>变化分解说明</summary>{explanation.notices.map(n => <p key={n}>{n}</p>)}</details>
    </section>
    <section className="bill-enhancement" aria-labelledby="bill-subscriptions"><h3 id="bill-subscriptions">订阅扣费核对与处理</h3><p className="hint">账户与账期沿用报告，消费分类筛选不影响本区。实际扣费与当前计划分开展示。</p>
      {subBusy && <p role="status">正在关联订阅及扣费流水…</p>}
      {subError && <p className="error" role="alert">{subError} <button onClick={() => setRetry(n => n + 1)}>重试</button></p>}
      {subscriptions && <>{subscriptions.items.length === 0 && <p className="bill-empty">未找到此范围的订阅关系或扣费</p>}{subscriptions.items.map(item => <article className={`bill-subscription-insight ${item.flags.length ? 'needs-review' : ''}`} key={`${item.account_id}-${item.merchant}`}>
        <h4>{item.merchant} <small>{item.account_id}</small></h4><p>本期实际 {money(item.current_fen)}／{item.current_count} 笔 · 上期实际 {money(item.previous_fen)}／{item.previous_count} 笔 · 当前人民币月费计划 {money(item.configured_monthly_fen)}</p>
        {item.explanations.map(n => <p key={n}>{n}</p>)}<Evidence current={item.current_evidence} previous={item.previous_evidence} onSelect={onSelect} selectedId={selectedId} />
        {item.active_subscription_ids.map(id => <button key={id} className="secondary" disabled={!onManage} onClick={() => onManage?.(id)}>核对取消详情 · {id}</button>)}
      </article>)}<details className="bill-notices"><summary>关联口径</summary>{subscriptions.notices.map(n => <p key={n}>{n}</p>)}</details></>}
    </section>
    <CashflowPanel accountId={result.query.account_id || null} revision={revision} />
  </>;
}

function CashflowPanel({ accountId, revision }: { accountId: string | null; revision: number }) {
  const initial = (): bills.CashflowScenario => ({ days: 30, account_id: accountId, reductions: [], excluded_subscription_ids: [] });
  const [draft, setDraft] = useState<bills.CashflowScenario>(initial);
  const [data, setData] = useState<bills.CashflowProjection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const chartRef = useRef<ReactECharts | null>(null);
  const chartHost = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!chartHost.current || busy || !data) return;
    let frame = 0;
    const resize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => chartRef.current?.getEchartsInstance().resize());
    };
    const observer = new ResizeObserver(resize);
    observer.observe(chartHost.current);
    window.addEventListener('resize', resize);
    resize();
    return () => { observer.disconnect(); window.removeEventListener('resize', resize); cancelAnimationFrame(frame); };
  }, [data, busy]);
  const load = async (scenario: bills.CashflowScenario) => {
    const id = ++generation.current;
    setError(''); setBusy(true);
    try { const result = await bills.cashflow(scenario); if (id === generation.current) setData(result); }
    catch (e) { if (id === generation.current) { setError(errorText(e)); setData(null); } }
    finally { if (id === generation.current) setBusy(false); }
  };
  useEffect(() => {
    const scenario = initial(); setDraft(scenario); setData(null); void load(scenario);
    return () => { generation.current++; };
  }, [accountId, revision, retry]);
  const updateReduction = (category: string, percent: number) => setDraft(s => ({ ...s, reductions: [...s.reductions.filter(r => r.category !== category), { category, percent }] }));
  const submit = (e: FormEvent) => { e.preventDefault(); void load(draft); };
  const dirty = data && JSON.stringify(draft) !== JSON.stringify(data.scenario);
  const subscriptionEvents = Array.from(new Map((data?.events || []).filter(e => e.source === 'subscription_plan' && e.reference_id).map(e => [e.reference_id!, e])).values());
  return <section className="bill-enhancement cashflow-panel" aria-labelledby="cashflow-title"><h3 id="cashflow-title">未来现金流与情景模拟</h3><p className="hint">从现实现在起算，与上方历史账期独立。仅分析有效储蓄现金；所有调整仅是本次假设。</p>
    <form onSubmit={submit}><div className="cashflow-controls"><label>预测天数<select value={draft.days} onChange={e => setDraft(s => ({ ...s, days: Number(e.target.value) }))}><option value={7}>未来7天</option><option value={30}>未来30天</option><option value={62}>未来62天</option></select></label><button disabled={busy}>{busy ? '正在计算…' : '计算情景'}</button><button type="button" className="secondary" disabled={busy} onClick={() => { const s = initial(); setDraft(s); void load(s); }}>恢复基准</button></div>
      <div className="cashflow-reductions">{Object.entries(labels).filter(([c]) => c !== 'subscription').map(([category, label]) => <label key={category}>{label}减少 {draft.reductions.find(r => r.category === category)?.percent || 0}%<input type="range" aria-label={`${label}减少比例`} min={0} max={100} step={5} value={draft.reductions.find(r => r.category === category)?.percent || 0} onChange={e => updateReduction(category, Number(e.target.value))} /></label>)}</div>
      {subscriptionEvents.length > 0 && <fieldset><legend>假设排除以下订阅扣费（不会实际取消）</legend>{subscriptionEvents.map(e => <label className="cashflow-check" key={e.reference_id}><input type="checkbox" checked={draft.excluded_subscription_ids.includes(e.reference_id!)} onChange={event => setDraft(s => ({ ...s, excluded_subscription_ids: event.target.checked ? [...s.excluded_subscription_ids, e.reference_id!] : s.excluded_subscription_ids.filter(id => id !== e.reference_id) }))} />{e.label} · 单次 {money(-e.amount_fen)}</label>)}</fieldset>}
    </form>
    {error && <p role="alert" className="error">{error} <button onClick={() => setRetry(n => n + 1)}>重试</button></p>}{busy && <p role="status">正在读取计划与历史样本…</p>}
    {data && !busy && <>{dirty && <p className="notice" role="status">假设已修改，请点击“计算情景”更新；下方仍是上一次计算结果。</p>}
      <p>截止 {time(data.as_of)}，预测至 {time(data.end_exclusive)}（不含终点）；{data.account_id || '全部有效储蓄账户'}。</p>
      <div className="bill-metrics">{[['当前可用现金', data.opening_available_fen], ['基准期末估计', data.baseline_end_fen], ['情景期末估计', data.scenario_end_fen], ['假设改善金额', data.scenario_saving_fen]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{money(Number(value))}</strong></div>)}</div>
      <p>当前占款 {money(data.held_fen)} · 计划流出 {money(data.planned_outflow_fen)} · 历史收入估计 {money(data.estimated_income_fen)} · 日常消费估计 {money(data.estimated_variable_spending_fen)}</p>
      <p className={data.first_negative_date ? 'notice' : 'hint'}>{data.first_negative_date ? `基准路径在 ${data.first_negative_date} 首次低于零，建议核对计划和估计。` : '本次日末基准路径未低于零；不代表没有日内或未知支出风险。'}</p>
      <div ref={chartHost} className="cashflow-chart"><ReactECharts ref={chartRef} echarts={echarts} style={{ height: 310 }} option={{ animation: false, aria: { enabled: true, description: `未来${data.days}天现金流估计，金额人民币元。基准期末${money(data.baseline_end_fen)}，假设情景期末${money(data.scenario_end_fen)}。` }, tooltip: { trigger: 'axis', valueFormatter: (v: number) => money(v) }, legend: { data: ['基准估计', '假设情景', '仅计划', '历史高支出', '历史低支出'], bottom: 0 }, grid: { left: 75, right: 15, bottom: 65, top: 25 }, xAxis: { type: 'category', data: data.timeline.map(p => p.date.slice(5)) }, yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `¥${v / 100}` } }, series: [
        { name: '基准估计', type: 'line', data: data.timeline.map(p => p.baseline_balance_fen), showSymbol: false },
        { name: '假设情景', type: 'line', data: data.timeline.map(p => p.scenario_balance_fen), showSymbol: false },
        { name: '仅计划', type: 'line', data: data.timeline.map(p => p.planned_balance_fen), showSymbol: false, lineStyle: { type: 'dashed' } },
        { name: '历史高支出', type: 'line', data: data.timeline.map(p => p.historical_low_balance_fen), showSymbol: false, lineStyle: { type: 'dotted', opacity: .5 } },
        { name: '历史低支出', type: 'line', data: data.timeline.map(p => p.historical_high_balance_fen), showSymbol: false, lineStyle: { type: 'dotted', opacity: .5 } },
      ], color: ['#1769e0', '#159a8c', '#815ac0', '#dd7184', '#e8a346'] }} /></div>
      <details className="insight-evidence"><summary>计划与估计依据 · {data.events.length} 项</summary>{data.events.map((e, i) => <p key={`${e.reference_id}-${i}`}>{time(e.at)} · {e.label} · {money(e.amount_fen)}{e.excluded_in_scenario && ' · 本次假设排除'}<small>{e.source === 'salary_estimate' ? `历史工资估计，依据 ${e.evidence_tx_ids.join('、')}` : '当前已保存计划，尚未执行'}</small></p>)}{data.spending_baselines.map(b => <p key={b.category}>{labels[b.category]}：{b.sample_count} 笔／{b.observed_months} 个月样本，日均约 {money(b.daily_fen)}；本期基准 {money(b.estimated_total_fen)}，假设 {money(b.scenario_total_fen)}。</p>)}</details>
      <details className="bill-notices"><summary>估计方法与数据不足说明</summary>{data.notices.map(n => <p key={n}>{n}</p>)}</details>
    </>}
  </section>;
}
