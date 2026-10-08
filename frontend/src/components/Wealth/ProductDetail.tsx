import { useEffect, useState } from 'react';
import * as w from '../../services/wealth';
import { beijingTime } from '../../utils/time';
export interface ProductDetailData {
  name:string;product_id:string;identity:string;category?:string;risk_level?:number|null;latest_nav?:{nav:string;date:string};as_of:string;source?:string|{url?:string;title?:string};
  rules?:{min_amount_fen?:number|null;holding_days?:number;cash_business_days?:number;purchase_fee_rate?:string|null;sales_service_fee_rate?:string|null;redeem_fee_bands?:{days_lt:number;rate:string}[];quick_redemption?:{start:string;end:string;daily_limit_fen:number;window:string}};
  money_yield?:{date:string;income_per_10000?:string|null;seven_day_annualized_percent?:string|null}[];
  chart:{points:{date:string;nav:string}[];window_start:string|null;window_end:string|null;notice:string;quality_flags:string[];
    metrics:Record<string,{value_percent:string|null;reason:string|null}>}|null;
}
export type ProductDetailLoader = (productId: string, period: string) => Promise<ProductDetailData>;
const bankDetail: ProductDetailLoader = (productId, period) => w.get<ProductDetailData>(`/products/${encodeURIComponent(productId)}/detail?period=${period}`);
export default function ProductDetail({productId,onClose,loadDetail=bankDetail}:{productId:string;onClose:()=>void;loadDetail?:ProductDetailLoader}) {
  const [period,setPeriod]=useState('1y');const [detail,setDetail]=useState<ProductDetailData|null>(null);const [error,setError]=useState('');const [hover,setHover]=useState(0);
  useEffect(()=>{let active=true;setDetail(null);setError('');
    loadDetail(productId,period).then(value=>{if(active){setDetail(value);setHover(Math.max(0,(value.chart?.points.length||1)-1));}})
      .catch(()=>{if(active)setError('暂时无法读取详情，请关闭后重试。');});return()=>{active=false;};},[productId,period,loadDetail]);
  const points=detail?.chart?.points||[];const values=points.map(p=>Number(p.nav));const min=Math.min(...values),max=Math.max(...values),delta=max-min||.01;
  const xy=(index:number)=>[20+(points.length===1?280:index/Math.max(1,points.length-1)*560),200-(values[index]-min)/delta*170];
  const selectPoint=(event:React.PointerEvent<SVGSVGElement>)=>{const rect=event.currentTarget.getBoundingClientRect();setHover(Math.max(0,Math.min(points.length-1,Math.round(((event.clientX-rect.left)/rect.width*600-20)/560*(points.length-1)))));};
  const labels:Record<string,string>={nav_change:'净值涨跌幅',max_drawdown:'最大回撤',volatility:'简单年化波动率'};
  const reasons:Record<string,string>={AT_LEAST_TWO_OBSERVATIONS:'至少需要2个净值点',AT_LEAST_TWENTY_RETURNS:'至少需要20个有效日收益',DATA_QUALITY_REVIEW_REQUIRED:'数据质量待核对'};
  const feeRate=(value:string|null|undefined)=>value==null?'未提供':`${(Number(value)*100).toLocaleString('zh-CN',{maximumFractionDigits:4})}%`;
  const categories:Record<string,string>={cash_management:'活钱',stable:'稳健',growth:'进取',cash:'活钱',bond:'债券',mixed:'混合',equity:'权益',index:'指数'};
  return <div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="产品详情" className="card wealth-detail"><button className="secondary" onClick={onClose}>关闭</button><h3>{detail?.name || productId}</h3>{error&&<p role="alert">{error}</p>}{!detail&&!error&&<p>正在读取已发布数据…</p>}
    {detail&&<><p>{detail.product_id} · {detail.identity}</p><p>{categories[detail.category || ''] || detail.category || '类别待核对'} · 风险 {detail.risk_level?`R${detail.risk_level}`:'未审核'}</p><p>已发布净值 {detail.latest_nav?.nav || '暂无'} · {detail.latest_nav?.date || '无有效净值日'}</p>
      <p>起购 {w.money(detail.rules?.min_amount_fen)} · 持有期 {detail.rules?.holding_days ?? '未审核'}天 · 普通到账 {detail.rules?.cash_business_days ?? '未审核'}营业日</p>
      <p>申购费率 {feeRate(detail.rules?.purchase_fee_rate)} · 销售服务费率 {feeRate(detail.rules?.sales_service_fee_rate)}</p>
      {detail.rules?.quick_redemption&&<p>模拟快赎：北京时间 {detail.rules.quick_redemption.start}—{detail.rules.quick_redemption.end} · {detail.rules.quick_redemption.window==='trading_day'?'交易日':'自然日'} · 每自然日限额 {w.money(detail.rules.quick_redemption.daily_limit_fen)}，成功不可撤。</p>}
      <div role="group" aria-label="净值观察范围">{[['1m','近一月'],['3m','近三月'],['1y','近一年']].map(([key,label])=><button key={key} className="secondary" aria-pressed={period===key} onClick={()=>setPeriod(key)}>{label}</button>)}</div>
      {!detail.chart?<><p>收益率资料不是单位净值，不计算这三个净值指标。</p>{detail.money_yield?.length ? <p>报告日 {detail.money_yield[detail.money_yield.length-1].date} · 万份收益 {detail.money_yield[detail.money_yield.length-1].income_per_10000 ?? '未知'}元 · 七日年化 {detail.money_yield[detail.money_yield.length-1].seven_day_annualized_percent ?? '未知'}%（历史报告值，不代表未来收益）</p>:<p>暂无已发布收益率报告。</p>}</>:<>{!points.length?<p>本窗口暂无已发布净值。</p>:<><p>实际观察 {detail.chart.window_start}—{detail.chart.window_end} · {points.length}点{points.length===1?'；仅一个点，不连线或计算涨跌':''}</p>
        <svg viewBox="0 0 600 230" className="wealth-nav-chart" role="img" aria-label="已发布单位净值走势" onPointerMove={selectPoint} onPointerDown={selectPoint}><line x1="20" y1="200" x2="580" y2="200" stroke="#9ca3af"/>{points.length>1&&<polyline points={points.map((_,index)=>xy(index).join(',')).join(' ')} fill="none" stroke="#3563e9" strokeWidth="3"/>}<circle cx={xy(hover)[0]} cy={xy(hover)[1]} r="5" fill="#3563e9"/><text x="20" y="225" fontSize="14">{points[0].date}</text><text x="580" y="225" textAnchor="end" fontSize="14">{points[points.length-1].date}</text></svg><p aria-live="polite">{points[hover]?.date} · 单位净值 {points[hover]?.nav}（移动鼠标或点按查看）</p></>}
        <div className="wealth-metric-grid">{Object.entries(labels).map(([key,label])=>{const metric=detail.chart?.metrics[key];return <article key={key}><strong>{label}</strong><p>{metric?.value_percent==null?'暂无':`${metric.value_percent}%`}</p><small>{metric?.reason?reasons[metric.reason] || metric.reason:key==='volatility'?'日收益样本标准差 × √252':key==='max_drawdown'?'窗口内峰值到谷值的最大跌幅':'末值相对首值的变化'}</small></article>;})}</div><p className="hint">{detail.chart.notice}</p>{detail.chart.quality_flags.length>0&&<p>本窗口数据质量待核对。</p>}</>}
      {detail.source && <p>来源：{typeof detail.source==='string'?<a href={detail.source} target="_blank" rel="noreferrer">原始资料</a>:detail.source.url?<a href={detail.source.url} target="_blank" rel="noreferrer">{detail.source.title || '官方规则参考资料'}</a>:'来源信息待核对'}</p>}
      <p className="hint">数据时点（北京时间）{beijingTime(detail.as_of)}。短历史按实际点展示，历史表现不代表未来回报。</p></>}
  </section></div>;
}
