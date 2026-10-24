import {useEffect,useState} from 'react';
import type {ReturnsQuery,WealthReturns,WealthPerformance,ReturnScenario,ReturnPeriod} from '../../services/wealthReturns';
import NavPerformanceChart from './NavPerformanceChart';
import './wealthReturns.css';
export interface ReturnsPreviewSource {load:(query:ReturnsQuery,scenario:string)=>Promise<WealthReturns>;scenarios:ReturnScenario[];notice:string;scenarioNotice:(scenario:string)=>string;defaultDate:string;actualBank?:boolean}
interface Props {source:ReturnsPreviewSource;initialProduct?:string;initialPeriod?:ReturnPeriod;initialDate?:string;onClose?:()=>void}
const periods:{id:ReturnPeriod;label:string}[]=[{id:'day',label:'昨日收益'},{id:'month',label:'近一月收益'},{id:'year',label:'近一年收益'},{id:'cumulative',label:'累计收益'}];
export default function ReturnsView({source,initialPeriod='day',onClose}:Props){
 const [period,setPeriod]=useState<ReturnPeriod>(initialPeriod),[data,setData]=useState<WealthPerformance|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
 useEffect(()=>{let live=true;setLoading(true);setData(null);setError('');
  void source.load({scope:'all',period,overview:true},'bank').then(r=>{if(live)setData(r.performance??null);}).catch(()=>{if(live)setError('收益暂时无法读取，请稍后重试。');}).finally(()=>{if(live)setLoading(false);});
  return()=>{live=false;};},[source,period]);
 const signed=(fen:number|null)=>fen===null?'待更新':`${fen>0?'+':fen<0?'-':''}¥${(Math.abs(fen)/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
 return <section className="wealth-returns" aria-label="整体理财表现"><div className="returns-heading"><h2>理财收益</h2>{onClose&&<button className="secondary" onClick={onClose}>收起收益</button>}</div>
  <label>收益统计<select aria-label="收益统计" value={period} onChange={e=>setPeriod(e.target.value as ReturnPeriod)}>{periods.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
  {loading&&<p role="status">正在读取收益…</p>}{error&&<p role="alert">{error}</p>}
  {data&&<><p className="returns-note">{data.start_date} — {data.end_date} · 全部理财产品，含已清仓收益</p>
   <dl className="returns-summary"><div><dt>{period==='cumulative'?'累计收益':'区间收益'}</dt><dd>{signed(data.amount_fen)}</dd></div><div><dt title="按期间实际投入、赎回及发生时间校正，非年化收益率">收益率</dt><dd>{data.rate_percent==null?(data.amount_fen===null?'待更新':'—'):`${Number(data.rate_percent)>0?'+':''}${Number(data.rate_percent).toFixed(2)}%`}</dd></div></dl>
   {data.notice&&<p role="status">{data.notice}</p>}
   <NavPerformanceChart performance={data.points.map(p=>({date:p.date,percent:p.rate_percent===null?null:Number(p.rate_percent)}))}/>
  </>}
 </section>;
}
