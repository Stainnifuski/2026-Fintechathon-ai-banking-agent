import {lazy,Suspense,useState} from 'react';
import type {NovaBillResult,BillResultTarget,BillResultMetric} from '../services/nova';
import './novaBillResults.css';
const ResultChart=lazy(()=>import('./NovaBillResultChart'));
export const billResultMoney=(value:number|null|undefined)=>value===null||value===undefined?'尚未核实':(value/100).toLocaleString('zh-CN',{style:'currency',currency:'CNY'});
const time=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
function Metrics({items}:{items:BillResultMetric[]}) {
 return <dl className="bill-result-metrics">{items.map((metric,index)=><div key={index}><dt>{metric.label}</dt><dd>{metric.amount_fen===null?(metric.text??'尚未核实'):billResultMoney(metric.amount_fen)}</dd></div>)}</dl>;
}
export default function NovaBillResults({card,onNavigate}:{card:NovaBillResult;onNavigate:(target:BillResultTarget)=>void}) {
 const [expanded,setExpanded]=useState(false);
 const rows=expanded?card.rows:card.rows.slice(0,5);
 return <article className="nova-bill-result" aria-label={`${card.title}结果卡`} data-bill-result={card.kind}>
  <header><div><span className="bill-result-eyebrow">BILL INSIGHTS</span><h3>{card.title}</h3></div><span className="bill-result-snapshot">查询时快照</span></header>
  <p className="bill-result-summary">{card.summary}</p>
  <div className="bill-result-scope">{card.scope.map((scope,index)=><span key={index}>{scope}</span>)}</div>
  <p className="bill-result-time">截至 {time(card.as_of)}（北京时间）</p>
  {!!card.metrics.length&&<Metrics items={card.metrics}/>}
  {card.charts.map((chart,index)=><Suspense key={index} fallback={<p role="status">正在加载图表…</p>}><ResultChart chart={chart} onNavigate={onNavigate}/></Suspense>)}
  {!!card.rows.length&&<div className="bill-result-rows">{rows.map((row,index)=><section key={index} className="bill-result-row">
    <div className="bill-result-row-head"><strong>{row.title}</strong>{row.badge&&<span className="bill-result-badge">{row.badge}</span>}</div>
    {row.description&&<p>{row.description}</p>}
    {!!row.metrics.length&&<Metrics items={row.metrics}/>}
    {row.progress!==null&&<div className="bill-result-progress"><progress aria-label={`${row.title}预算使用比例`} max={100} value={Math.max(0,Math.min(100,row.progress))}/><span>已用 {row.progress}%</span></div>}
    {row.target&&<button type="button" className="secondary" onClick={()=>onNavigate(row.target!)}>查看关联工作台 ↗</button>}
  </section>)}</div>}
  {card.rows.length>5&&<button type="button" className="secondary" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'收起明细':`显示其余 ${card.rows.length-5} 项`}</button>}
  {card.total!==null&&<p className="bill-result-count">本次返回 {card.rows.length} 项 · 查询范围内共 {card.total} 项{card.has_more?' · 仍有更多结果，可继续查询或在工作台查看':''}</p>}
  {!card.rows.length&&!card.charts.length&&!card.metrics.length&&<p className="bill-result-empty">本次查询范围内暂无匹配记录</p>}
  {!!card.notices.length&&<aside className="bill-result-notices" aria-label="数据口径与提示">{card.notices.map((notice,index)=><p key={index}>{notice}</p>)}</aside>}
  <footer><small>历史快照保留查询时数据</small><button type="button" className="secondary" onClick={()=>onNavigate(card.target)}>到工作台查看最新数据 ↗</button></footer>
 </article>;
}
