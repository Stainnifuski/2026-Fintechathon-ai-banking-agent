import { FormEvent, useEffect, useRef, useState } from 'react';
import ReactECharts from './Chart';
import * as echarts from 'echarts/core';
import { PieChart, LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, AriaComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { NovaPageContext } from '../../services/nova';
import type { Account } from '../../services/bank';
import * as bills from '../../services/bills';
import './styles.css';
import Enhancements from './Enhancements';
import {BudgetPanel,AnomalyPanel} from './Decisions';
import Foundations, { TransactionAnnotations } from './Foundations';
import { accountLabel, counterpartyLabel } from '../../utils/customerLabels';

echarts.use([PieChart, LineChart, GridComponent, TooltipComponent, AriaComponent, LegendComponent, CanvasRenderer]);
const labels: Record<string, string> = { dining: '餐饮', shopping: '购物', transport: '交通', entertainment: '娱乐', subscription: '订阅', education: '学习', other: '其他' };
const types: Record<string, string> = { investment_purchase: '投资申购', investment_redemption: '投资赎回', salary: '工资', purchase: '消费', subscription: '订阅扣费', refund: '退款', transfer_in: '转入', transfer_out: '转出' };
const money = (fen: number) => (fen / 100).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' });
const dateTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
const currentMonth = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit' }).format(new Date());
const initialQuery = (): bills.BillQuery => ({ period: 'month', date: currentMonth() });
const errorText = (error: unknown) => {
  const detail = (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail;
  return detail?.message || '账单加载失败，请重试。';
};

export default function BillAnalysis({ accounts, revision, onViewCashflows, onContextChange, onManageSubscription, onAskNova }: { accounts: Account[]; revision: number; onViewCashflows?: () => void; onContextChange?: (context: NovaPageContext) => void; onManageSubscription?: (id: string) => void; onAskNova: (text: string, context: NovaPageContext) => void }) {

  const [metadataRevision, setMetadataRevision] = useState(0);
  const [catalog, setCatalog] = useState<bills.FoundationCatalog | null>(null);
  const [draft, setDraft] = useState<bills.BillQuery>(initialQuery);
  const applied = useRef<bills.BillQuery>(initialQuery());
  const [result, setResult] = useState<bills.BillAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [detailFilter, setDetailFilter] = useState<{ category?: string; tx_ids?: string; label: string }>({ label: '报告内已完成明细' });
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<bills.BillPage | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailRetry, setDetailRetry] = useState(0);
  const sequence = useRef(0);
  const [selectedTransaction, setSelectedTransaction] = useState<string | null>(null);
  useEffect(() => {
    onContextChange?.({ version: 1, page: 'insights',
      ...(result && !busy ? {
        filters: result.query,
        observed_as_of: result.as_of,
        details: { category: detailFilter.category, tx_ids: detailFilter.tx_ids?.split(',').slice(0, 10) || [], page },
        ...(selectedTransaction ? { selected: { type: 'transaction' as const, id: selectedTransaction } } : {}),
      } : {}),
    });
  }, [result, busy, detailFilter, page, selectedTransaction, onContextChange]);
  useEffect(() => { setSelectedTransaction(null); }, [result, detailFilter, page]);

  const accept = (data: bills.BillAnalysis) => {
    applied.current = data.query;
    setDraft(data.query); setResult(data); setPage(1);
    setDetailFilter({ label: data.query.category ? `${labels[data.query.category]}消费` : '报告内已完成明细' });
  };
  const load = async (query: bills.BillQuery) => {
    const id = ++sequence.current;
    setBusy(true); setError('');
    try { const data = await bills.analysis(query); if (id === sequence.current) accept(data); }
    catch (e) { if (id === sequence.current) { setError(errorText(e)); setResult(null); } }
    finally { if (id === sequence.current) setBusy(false); }
  };
  useEffect(() => {
    let cancelled = false;
    bills.foundations().then(data => { if (!cancelled) setCatalog(data); }).catch(() => {});
    return () => { cancelled = true; };
  }, [revision, metadataRevision]);
  useEffect(() => {
    void load(applied.current);
    return () => { sequence.current++; };
  }, [revision, metadataRevision]); // refresh after bank activity; request generation prevents stale responses

  useEffect(() => {
    if (!result) { setRows(null); return; }
    let cancelled = false;
    setRows(null); setDetailError(''); setDetailBusy(true);
    bills.details({ ...result.query, category: detailFilter.category || result.query.category }, page, result.as_of, detailFilter.tx_ids)
      .then(data => { if (!cancelled) setRows(data); })
      .catch(e => { if (!cancelled) setDetailError(errorText(e)); })
      .finally(() => { if (!cancelled) setDetailBusy(false); });
    return () => { cancelled = true; };
  }, [result, detailFilter, page, detailRetry]);

  const ask = (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim() || busy || !result) return;
    onAskNova(question.trim(), { version: 1, page: 'insights', filters: result.query,
      observed_as_of: result.as_of,
      details: { category: detailFilter.category, tx_ids: detailFilter.tx_ids?.split(',').slice(0, 10) || [], page },
      ...(selectedTransaction ? {selected: {type:'transaction' as const, id:selectedTransaction}} : {}),
    });
    setQuestion('');
  };
  const drill = (category?: string, tx_ids?: string, label = '报告内已完成明细') => {
    setPage(1); setDetailFilter({ category, tx_ids, label });
    document.getElementById('bill-transactions')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  return <section className="card bills" aria-labelledby="bill-title">
    <div className="bill-heading"><div><p className="bill-eyebrow">YOUR MONEY, IN VIEW</p><h2 id="bill-title">账单分析</h2><p className="hint">看清消费去向，核对每一笔变化。金额单位：人民币。</p></div><span className="bill-badge">账单洞察</span></div>
    <p className="hint">本报告不计入理财申购和赎回的金额、笔数及明细。{onViewCashflows && <button type="button" className="text-button" onClick={onViewCashflows}>查看资金流水</button>}</p>
    <form className="bill-filters" onSubmit={e => { e.preventDefault(); void load(draft); }}>
      <label>报告类型<select value={draft.period} onChange={e => { const period = e.target.value as 'month' | 'year'; setDraft({ ...draft, period, date: period === 'year' ? draft.date.slice(0, 4) : `${draft.date.slice(0, 4)}-${currentMonth().slice(5)}` }); }}><option value="month">月度账单</option><option value="year">年度账单</option></select></label>
      <label>{draft.period === 'month' ? '月份' : '年份'}<input aria-label="账单时间" required type={draft.period === 'month' ? 'month' : 'number'} min={draft.period === 'month' ? '2000-01' : '2000'} max={draft.period === 'month' ? currentMonth() : currentMonth().slice(0, 4)} value={draft.date} onChange={e => setDraft({ ...draft, date: e.target.value })} /></label>
      <label>账户<select value={draft.account_id || ''} onChange={e => setDraft({ ...draft, account_id: e.target.value || null })}><option value="">全部账户</option>{accounts.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>
      <label>消费分类<select value={draft.category || ''} onChange={e => setDraft({ ...draft, category: e.target.value || null })}><option value="">全部分类及收支</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>用户标签<select aria-label="用户标签" value={draft.tag_id || ''} onChange={e => setDraft({ ...draft, tag_id: e.target.value || null })}><option value="">全部标签</option>{catalog?.tags.map(t => <option key={t.tag_id} value={t.tag_id}>{t.name} · {t.transaction_count} 笔</option>)}</select></label>
      <button disabled={busy}>{busy ? '正在分析…' : '查看账单'}</button>
    </form>
    <form className="bill-question" onSubmit={ask}><label htmlFor="bill-question">向 NOVA 提问账单</label><div className="row"><input id="bill-question" maxLength={500} value={question} onChange={e => setQuestion(e.target.value)} placeholder="这个月为什么比上个月花得多？" /><button disabled={busy || !result || !question.trim()}>交给 NOVA 分析</button></div><p className="hint">在 NOVA 对话中分析账单、核对异常、查看预算和预测，也可整理分类、标签与商户。沿用当前已应用的筛选和选中流水；修改需在确认卡核对后保存。</p></form>
    <Foundations accounts={accounts} revision={revision + metadataRevision} onChanged={() => setMetadataRevision(n => n + 1)} onManage={onManageSubscription} />
    {error && <p className="error" role="alert">{error}</p>}
    {busy && <p role="status">正在读取完整账期数据…</p>}
    {result && !busy && <>
      <div className="bill-period"><strong>{result.query.date} · {result.query.period === 'month' ? '月度' : '年度'}报告{result.query.category && ` · ${labels[result.query.category]}消费`}</strong><span>{result.query.account_id ? accountLabel(result.query.account_id, accounts) : '全部账户'} · {result.partial ? '本期尚未结束，与上期同期比较' : '完整账期'}</span><small>北京时间：{dateTime(result.start)} 至 {dateTime(result.end_exclusive)}（不含终点）</small></div>
      <div className="bill-metrics">{[
        ['消费原额', result.totals.consumption_fen], ['报告流入', result.totals.inflow_fen],
        ['报告流出', result.totals.outflow_fen], ['报告净流入', result.totals.net_inflow_fen],
      ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{money(Number(value))}</strong></div>)}</div>
      <div className="bill-submetrics"><span>退款 {money(result.totals.refund_fen)}（已关联 {money(result.totals.linked_refund_fen)} · 未关联 {money(result.totals.unlinked_refund_fen)}）</span><span>净消费 {money(result.totals.net_consumption_fen)}</span><span>转入 {money(result.totals.transfer_in_fen)}</span><span>转出 {money(result.totals.transfer_out_fen)}</span><span>已完成 {result.totals.count} 笔</span></div>
      <p className="bill-comparison">消费较上期变化 <strong>{money(result.change_fen)}</strong> · {result.change_percent === null ? '上期为零，不计算增长率' : `${result.change_percent > 0 ? '+' : ''}${result.change_percent.toFixed(2)}%`}<small>对比范围：{dateTime(result.previous_start)} 至 {dateTime(result.previous_end_exclusive)}（不含终点）</small></p>
      {result.totals.count === 0 && <p className="bill-empty">这个范围内暂无纳入报告的已完成交易。可以切换月份或账户查看。</p>}
      <div className="bill-charts"><div><h3>消费去向</h3>{result.totals.consumption_fen ? <ReactECharts echarts={echarts} style={{ height: 250 }} option={{ aria: { enabled: true, description: `消费分类：${result.categories.map(c => `${c.label}${money(c.amount_fen)}`).join('，')}。点击分类可查看流水。` }, tooltip: { trigger: 'item', valueFormatter: (v: number) => money(v) }, series: [{ type: 'pie', radius: ['43%', '68%'], label: { formatter: '{b}\n{d}%' }, data: result.categories.filter(c => c.amount_fen > 0).map(c => ({ name: c.label, value: c.amount_fen, category: c.category })) }], color: ['#1769e0', '#159a8c', '#e8a346', '#815ac0', '#dd7184', '#899bb6'] }} onEvents={{ click: (params: { data: { category: string }; name: string }) => drill(params.data.category, undefined, `${params.name}消费`) }} /> : <p className="bill-empty">暂无消费数据</p>}</div>
        <div><h3>{result.query.period === 'month' ? '每日' : '每月'}消费趋势</h3><ReactECharts echarts={echarts} style={{ height: 250 }} option={{ aria: { enabled: true, description: `消费趋势，金额单位人民币元：${result.trend.map(p => `${p.date} ${money(p.consumption_fen)}`).join('，')}` }, tooltip: { trigger: 'axis', valueFormatter: (v: number) => money(v) }, grid: { left: 65, right: 15, bottom: 35, top: 25 }, xAxis: { type: 'category', data: result.trend.map(p => p.date.slice(5)) }, yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `¥${v / 100}` } }, series: [{ name: '消费原额', type: 'line', data: result.trend.map(p => p.consumption_fen), itemStyle: { color: '#1769e0' }, areaStyle: { color: '#e8f1ff' }, showSymbol: result.trend.length < 15 }] }} /></div></div>
      <div className="bill-category-list">{result.categories.map(c => <button className="secondary" key={c.category} onClick={() => drill(c.category, undefined, `${c.label}消费`)}>{c.label} · {money(c.amount_fen)} · {c.share_percent}% · {c.count} 笔 · 关联退款 {money(c.refund_fen)} · 净额 {money(c.net_amount_fen)}</button>)}</div>
      <BudgetPanel month={result.query.period==='month'?result.query.date:currentMonth()} accounts={accounts} revision={revision+metadataRevision} onChanged={()=>setMetadataRevision(n=>n+1)}/>
      <Enhancements result={result} revision={revision + metadataRevision} selectedId={selectedTransaction} onSelect={id => setSelectedTransaction(current => current === id ? null : id)} onManage={onManageSubscription} />
      <div className="bill-report"><h3>账单总结</h3>{result.report.map(line => <p key={line}>{line}</p>)}</div>
      <AnomalyPanel result={result} onChanged={()=>setMetadataRevision(n=>n+1)} onDrill={item=>drill(item.category||undefined,item.tx_ids.join(',')||undefined,item.title)}/>
      <div id="bill-transactions"><div className="bill-heading"><h3>{detailFilter.label}</h3><button className="secondary" onClick={() => drill(undefined, undefined, result.query.category ? `${labels[result.query.category]}消费` : '报告内已完成明细')}>重置明细筛选</button></div>
        {detailBusy && <p role="status">正在加载流水…</p>}{detailError && <p role="alert" className="error">{detailError} <button onClick={() => setDetailRetry(n => n + 1)}>重试</button></p>}
        {rows && <><div className="bill-table"><table><thead><tr><th>时间</th><th>交易对手 / 备注</th><th>类型 / 账户</th><th>金额</th></tr></thead><tbody>{rows.items.map(tx => <tr key={tx.tx_id}><td>{dateTime(tx.timestamp)}<button type="button" className="secondary" aria-pressed={selectedTransaction === tx.tx_id} onClick={() => setSelectedTransaction(current => current === tx.tx_id ? null : tx.tx_id)}>{selectedTransaction === tx.tx_id ? '已选中 · 可问 NOVA' : '选择这笔'}</button></td><td>{counterpartyLabel(tx.counterparty)}<small>{tx.note || '无备注'}</small><TransactionAnnotations tx={tx} onSaved={() => setMetadataRevision(n => n + 1)} /></td><td>{types[tx.type] || '交易'}<small>{accountLabel(tx.account_id, accounts)}</small></td><td>{['purchase', 'subscription', 'transfer_out', 'investment_purchase'].includes(tx.type) ? '−' : ['salary', 'refund', 'transfer_in', 'investment_redemption'].includes(tx.type) ? '+' : ''}{money(tx.amount_fen)}</td></tr>)}</tbody></table></div>{!rows.total && <p className="bill-empty">暂无符合筛选条件的流水</p>}<div className="bill-pagination"><span>共 {rows.total} 笔 · 第 {rows.page} / {Math.max(1, Math.ceil(rows.total / rows.page_size))} 页</span><button className="secondary" disabled={page <= 1 || detailBusy} onClick={() => setPage(n => n - 1)}>上一页</button><button className="secondary" disabled={page * rows.page_size >= rows.total || detailBusy} onClick={() => setPage(n => n + 1)}>下一页</button></div></>}
      </div>
      <details className="bill-notices"><summary>统计口径与数据说明</summary><ul>{result.notices.map(line => <li key={line}>{line}</li>)}</ul><p>数据截止：{dateTime(result.as_of)}。报告按交易时间查询，未保存历史快照；稍后补录的数据可能改变结果。</p></details>
    </>}
  </section>;
}
