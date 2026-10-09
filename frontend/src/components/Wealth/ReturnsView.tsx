import {useEffect,useState} from 'react';
import type {ReturnsQuery,WealthReturns,ReturnScenario,ReturnPeriod} from '../../services/wealthReturns';
import {calendarCells,returnMoney,returnStatus} from '../../services/wealthReturns';
import './wealthReturns.css';

export interface ReturnsPreviewSource {
  load:(query:ReturnsQuery,scenario:string)=>Promise<WealthReturns>;
  scenarios:ReturnScenario[];notice:string;scenarioNotice:(scenario:string)=>string;defaultDate:string;
}
interface Props {source:ReturnsPreviewSource;initialProduct?:string;onClose:()=>void}
const periods:{id:ReturnPeriod;label:string}[]=[{id:'day',label:'日'},{id:'month',label:'月'},{id:'year',label:'年'},{id:'cumulative',label:'累计'}];
export default function ReturnsView({source,initialProduct,onClose}:Props){
  const [scenario,setScenario]=useState(source.scenarios[0].id);
  const [product,setProduct]=useState(initialProduct??'');
  const [period,setPeriod]=useState<ReturnPeriod>('day');
  const [date,setDate]=useState(source.defaultDate);
  const [result,setResult]=useState<WealthReturns|null>(null);
  const [options,setOptions]=useState<{product_id:string;product_name:string}[]>([]);
  const [error,setError]=useState('');const [loading,setLoading]=useState(false);
  useEffect(()=>{
    let active=true;setLoading(true);setError('');setResult(null);
    const query:ReturnsQuery={scope:'all',period,...(period==='cumulative'?{}:{anchor_date:date})};
    void source.load(query,scenario).then(async all=>{
      const data=product?await source.load({...query,scope:'product',product_id:product},scenario):all;
      if(active){setOptions(all.products);setResult(data);}
    }).catch((reason:unknown)=>{if(active)setError(reason instanceof Error?reason.message:'收益资料查询失败，请重试。');})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[source,scenario,product,period,date]);
  const chooseScenario=(id:string)=>{setScenario(id);setProduct('');setOptions([]);};
  return <section className="wealth-returns" aria-label="理财收益样例视图">
    <div className="returns-heading"><div><h2>理财收益</h2><p>含已赎回的已实现收益。收益金额与产品净值涨跌分别查看。</p></div><button className="secondary" onClick={onClose}>返回持仓表</button></div>
    <p className="consumer-warning">B3d · 收益界面开发预览。{source.notice}</p>
    <div className="returns-controls"><label>收益样例<select aria-label="收益样例" value={scenario} onChange={e=>chooseScenario(e.target.value)}>{source.scenarios.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <label>统计范围<select aria-label="统计范围" value={product} onChange={e=>setProduct(e.target.value)}><option value="">整体收益（含历史产品）</option>{options.map(row=><option key={row.product_id} value={row.product_id}>{row.product_name} · {row.product_id}</option>)}</select></label>
      <label>查询日期（北京时间）<input type="date" value={date} disabled={period==='cumulative'} onChange={e=>{if(e.target.value)setDate(e.target.value);}}/></label>
    </div>
    <div className="returns-periods" aria-label="收益时间粒度">{periods.map(item=><button className="secondary" key={item.id} aria-pressed={period===item.id} onClick={()=>setPeriod(item.id)}>{item.label}</button>)}</div>
    <p className="returns-note">{source.scenarioNotice(scenario)}</p>
    {loading&&<p role="status">正在读取收益样例…</p>}{error&&<p role="alert">{error}</p>}
    {result&&<>
      <p>{result.start_date} 至 {result.end_date} · {returnStatus[result.status]}</p>
      <dl className="returns-summary"><div><dt>所选期间收益</dt><dd>{returnMoney(result.summary.period_pnl_fen)}</dd></div><div><dt>累计收益（截至所选期末）</dt><dd>{returnMoney(result.summary.cumulative_pnl_fen)}</dd></div><div><dt>累计已实现</dt><dd>{returnMoney(result.summary.realized_pnl_fen)}</dd></div><div><dt>累计未实现</dt><dd>{returnMoney(result.summary.unrealized_pnl_fen)}</dd></div></dl>
      <p className="returns-note">期间费用明细：{returnMoney(result.summary.fees_fen)}。已反映在净值或实际现金流中的费用不再重复扣减。累计已实现／未实现为期末口径，不与期间收益混加。</p>
      <p className="returns-note">可核实覆盖起点：{result.coverage_start??'未知'} · 估值资料截至 {result.valuation_as_of??'未知'}。覆盖外与缺资料的日期不补零；本期未结束时仅展示已覆盖部分。</p>
      {period==='month'&&<><h3>每日收益日历</h3><div className="returns-calendar" aria-label="每日收益日历">{['一','二','三','四','五','六','日'].map(day=><span className="returns-weekday" key={day}>{day}</span>)}{calendarCells(result.points,date.slice(0,7)).map((point,index)=>point?<button className="returns-day" key={point.period_start} title={point.reason??returnStatus[point.status]} aria-label={`${point.period_start}收益 ${point.pnl_fen===null?'暂无完整数据':returnMoney(point.pnl_fen)}`} onClick={()=>{setDate(point.period_start);setPeriod('day');}}><span>{Number(point.period_start.slice(-2))}</span><strong>{point.pnl_fen===null?'待资料':returnMoney(point.pnl_fen)}</strong></button>:<span key={`blank-${index}`}/>)}</div></>}
      <h3>{period==='year'?'逐月收益':period==='cumulative'?'逐年收益':'收益时间序列'}</h3>
      <div className="returns-table-scroll"><table aria-label="收益时间序列表"><thead><tr><th>时段</th><th>收益金额</th><th>完整性／说明</th></tr></thead><tbody>{result.points.map(point=><tr key={point.period_start}><td>{point.period_start}{point.period_end!==point.period_start&&` 至 ${point.period_end}`}</td><td>{returnMoney(point.pnl_fen)}</td><td>{returnStatus[point.status]}{point.reason&&` · ${point.reason}`}</td></tr>)}</tbody></table></div>
      <h3>具体份额贡献（全量）</h3><div className="returns-table-scroll"><table aria-label="分产品收益贡献"><thead><tr><th>产品／份额代码</th><th>期间收益</th><th>累计收益</th></tr></thead><tbody>{result.products.map(row=><tr key={row.product_id}><td><button className="returns-product-link" onClick={()=>setProduct(row.product_id)}>{row.product_name}</button><small>{row.product_id}{row.reason&&` · ${row.reason}`}</small></td><td>{returnMoney(row.period_pnl_fen)}</td><td>{returnMoney(row.cumulative_pnl_fen)}</td></tr>)}</tbody></table></div>
      <details className="returns-note"><summary>数据来源与计算版本</summary><p>{result.data_source} · {result.formula_version}</p><p>查询时点：{result.as_of} · 范围 {result.asset_scope_id} · 收益事实版本 {result.snapshot_id}</p><p>当前只验证界面消费契约录制；真实银行历史重建及收益接口尚未接入，不代表 F20—F24 账务验收完成。</p></details>
    </>}
  </section>;
}
