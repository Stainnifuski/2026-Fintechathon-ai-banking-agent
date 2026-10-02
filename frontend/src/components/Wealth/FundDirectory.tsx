import { useState } from 'react';
import ReactECharts from 'echarts-for-react';
import * as w from '../../services/wealth';
interface Identity { code: string; name: string; fund_type: string; available_at?: string }
interface Evidence { status: string; stale: boolean; error_code: string | null; data: { fields?: Record<string, { ok: boolean; records?: Record<string, unknown>[]; error_type?: string }>; observations?: { date: string; nav?: string; income_per_10000?: string; seven_day_annualized_percent?: string }[]; source_url?: string; available_at?: string } }
interface Detail extends Identity { evidence: Record<string, Evidence>; on_demand_enabled: boolean }
const fieldNames: Record<string, string> = { annual_fees: '年度费用', purchase_fees: '申购费率', redemption_fees: '赎回费率', amount_limits: '申赎金额限制', confirmation: '确认与到账说明' };
export default function FundDirectory() {
  const [query, setQuery] = useState(''); const [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: Identity[]; total: number } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const search = async (next = 1) => { setBusy(true); setError(''); try { setData(await w.post('/funds/search', { query, page: next })); setPage(next); } catch { setError('目录读取失败；不会返回模拟替代数据。'); } finally { setBusy(false); } };
  const load = async (code: string, fetch_missing = true) => { setBusy(true); setError(''); try { setDetail(await w.post('/funds/detail', { code, fetch_missing })); } catch { setError('详情不可用；保留已有成功数据。'); } finally { setBusy(false); } };
  const evidence = detail?.evidence.history || detail?.evidence.money_yield;
  const money = !!detail?.evidence.money_yield;
  return <details><summary>全量真实基金目录与池外研究</summary><p>预加载池没有搜索上限。目录记录不代表可申购，缺少审核交易规则的基金仅供研究；首次详情获取在后台缓存。</p>
    <label>基金代码、名称<input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void search(); }} /></label><button disabled={busy} onClick={() => void search()}>检索全量目录</button>
    {error && <p role="alert">{error}</p>}<p>目录匹配 {data?.total ?? '未查询'} 条</p>
    {data?.items.map(p => <p key={p.code}>{p.code} · {p.name} · {p.fund_type} · 真实基金数据 <button disabled={busy} onClick={() => void load(p.code)}>详情与获取状态</button></p>)}
    {!!data && <div className="wealth-actions"><button disabled={busy || page === 1} onClick={() => void search(page - 1)}>目录上一页</button><span>{page}</span><button disabled={busy || page * 20 >= data.total} onClick={() => void search(page + 1)}>目录下一页</button></div>}
    {detail && <section><h4>{detail.name} · {detail.code} · 真实基金数据</h4><p>仅研究，未知风险、费用分档或到账规则不会补零或生成可执行方案。</p>{Object.entries(detail.evidence).map(([type, e]) => <p key={type}>{type}：{e.status}{e.stale ? ' · 陈旧' : ''} {e.error_code} · 采集 {e.data.available_at || '尚无成功数据'} {e.data.source_url && <a href={e.data.source_url} target="_blank" rel="noreferrer">来源</a>}</p>)}
      {!detail.on_demand_enabled && <p>联网详情未启用；管理员可通过CLI预取，不使用模拟值替代。</p>}<button disabled={busy} onClick={() => void load(detail.code, false)}>刷新获取状态</button>
      {detail.evidence.fees?.data.fields && <details><summary>来源费率与确认规则（未审定）</summary>{Object.entries(detail.evidence.fees.data.fields).map(([key, field]) => <section key={key}><h5>{fieldNames[key] || key}</h5>{field.ok ? field.records?.map((row,i) => <p key={i}>{Object.values(row).map(v => v == null ? '未提供' : String(v)).join(' · ')}</p>) : <p>资料缺失：{field.error_type}</p>}</section>)}</details>}
      {!!evidence?.data.observations?.length && <><p>{money ? '万份收益（元/万份）与七日年化（百分比）是独立指标，不是单位净值或保证收益' : '单位净值变动，未核实分红不称为总回报'}</p><ReactECharts option={{ tooltip: { trigger: 'axis' }, xAxis: { type: 'time' }, yAxis: { type: 'value', scale: true }, dataZoom: [{ type: 'inside' }], series: [{ type: 'line', showSymbol: false, data: evidence.data.observations.map(o => [o.date, money ? o.income_per_10000 : o.nav]) }] }} style={{ height: 260 }} />{money && <p>最近七日年化：{evidence.data.observations[evidence.data.observations.length - 1]?.seven_day_annualized_percent ?? '未知'}%</p>}</>}
    </section>}
  </details>;
}
