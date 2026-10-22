import { useEffect, useState } from 'react';
import * as w from '../../services/wealth';
import { beijingTime } from '../../utils/time';
import NavPerformanceChart from './NavPerformanceChart';
export interface ProductDetailData {
  risk_source_kind?:string|null;project_policy_version?:string|null;public_risk_rating?:unknown;public_nav_date_basis?:string|null;execution_source_kind?:string;
  name:string;product_id:string;identity:string;category?:string;risk_level?:number|null;latest_nav?:{nav:string;date:string};as_of:string;source?:string|{url?:string;title?:string};
  rules?:{min_amount_fen?:number|null;holding_days?:number;cash_business_days?:number;purchase_fee_rate?:string|null;sales_service_fee_rate?:string|null;redeem_fee_bands?:{days_lt:number;rate:string}[];quick_redemption?:{start:string;end:string;daily_limit_fen:number;window:string}};
  execution_price?:{value:string;date:string;source:string};simulated_unit_price?:string;history_source?:string;charts?:Record<string,ProductDetailData['chart']>;
  chart:{points:{date:string;nav:string}[];window_start:string|null;window_end:string|null;notice:string;quality_flags:string[];
    metrics:Record<string,{value_percent:string|null;reason:string|null}>}|null;
}
export type ProductDetailLoader = (productId: string, period: string, productVersion?:number) => Promise<ProductDetailData>;
const bankDetail: ProductDetailLoader = (productId, period, version) => w.get<ProductDetailData>(`/products/${encodeURIComponent(productId)}/detail?period=${period}${version?`&product_version=${version}`:''}`);
export default function ProductDetail({productId,productVersion,onClose,loadDetail=bankDetail}:{productId:string;productVersion?:number;onClose:()=>void;loadDetail?:ProductDetailLoader}) {
  const [period,setPeriod]=useState('1y');const [detail,setDetail]=useState<ProductDetailData|null>(null);const [error,setError]=useState('');
  useEffect(()=>{let active=true;setDetail(null);setError('');
    loadDetail(productId,'1y',productVersion).then(value=>{if(active){setDetail(value);}})
      .catch(()=>{if(active)setError('暂时无法读取该版本详情，请关闭后重试。');});return()=>{active=false;};},[productId,productVersion,loadDetail]);
  const activeChart=detail?.charts?detail.charts[period]:detail?.chart;
  const points=activeChart?.points||[];
  const labels:Record<string,string>={nav_change:'净值涨跌幅',max_drawdown:'最大回撤',volatility:'简单年化波动率'};
  const reasons:Record<string,string>={AT_LEAST_TWO_OBSERVATIONS:'至少需要2个净值点',AT_LEAST_TWENTY_RETURNS:'至少需要20个有效日收益',DATA_QUALITY_REVIEW_REQUIRED:'数据质量待核对'};
  const feeRate=(value:string|null|undefined)=>value==null?'未提供':`${(Number(value)*100).toLocaleString('zh-CN',{maximumFractionDigits:4})}%`;
  const categories:Record<string,string>={cash_management:'活钱',stable:'稳健',growth:'进取',cash:'活钱',bond:'债券',mixed:'混合',equity:'权益',index:'指数'};
  return <div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="产品详情" className="card wealth-detail"><button className="secondary" onClick={onClose}>关闭</button><h3>{detail?.name || '产品详情'}</h3>{error&&<p role="alert">{error}</p>}{!detail&&!error&&<p>正在读取已发布数据…</p>}
    {detail&&<><p>{detail.identity}</p><p>{categories[detail.category || ''] || '类别待核对'} · {detail.risk_source_kind==='project_policy'?'项目等级（独立于发行人评级）':'风险'} {detail.risk_level?`R${detail.risk_level}`:'未审核'}</p>{detail.execution_price?<p>预览参考净值 {detail.execution_price.value} · 来源净值日期 {detail.execution_price.date}；交易前自动核对最新有效净值；本人确认与MFA后即时模拟成交。</p>:detail.simulated_unit_price?<p>项目模拟计价 {detail.simulated_unit_price}元／份；真实单位NAV未核实，收益率不转换成NAV。</p>:detail.latest_nav&&<p>已发布净值 {detail.latest_nav.nav} · {detail.latest_nav.date}</p>}
      <p>本金可能亏损；R1和快赎均不代表本金或收益保证。公开更高评级或分类冲突不得降级。</p>
      {detail.public_nav_date_basis?.includes('MM-DD')&&<p>原报告只有月日；年份按观测日前最近日期解释，原值与来源指纹已保留。</p>}
      <p>起购 {w.money(detail.rules?.min_amount_fen)} · 持有期 {detail.rules?.holding_days ?? '未审核'}天 · 普通到账 {detail.rules?.cash_business_days ?? '未审核'}营业日</p>
      <p>{detail.risk_source_kind==='project_policy'?'项目模拟费用：':''}申购费率 {feeRate(detail.rules?.purchase_fee_rate)} · 销售服务费率 {feeRate(detail.rules?.sales_service_fee_rate)}</p>
      {detail.rules?.quick_redemption&&<p>模拟快赎：北京时间 {detail.rules.quick_redemption.start}—{detail.rules.quick_redemption.end} · {detail.rules.quick_redemption.window==='trading_day'?'交易日':'自然日'} · 每自然日限额 {w.money(detail.rules.quick_redemption.daily_limit_fen)}，成功不可撤。</p>}
      <div role="group" aria-label="净值观察范围">{[['1m','近一月'],['3m','近三月'],['1y','近一年']].map(([key,label])=><button key={key} className="secondary" aria-pressed={period===key} onClick={()=>{setPeriod(key);}}>{label}</button>)}</div>
      {!activeChart?<><p>当前没有足够的可靠单位净值历史，图表和不足指标已隐藏。收益率不代替NAV。</p></>:<>{points.length<2?<p>本窗口历史不足，图表已隐藏。</p>:<><p>实际观察 {activeChart.window_start}—{activeChart.window_end} · {points.length}点{points.length===1?'；仅一个点，不连线或计算涨跌':''}</p>
        <NavPerformanceChart key={period} points={points}/></>}
        <div className="wealth-metric-grid">{Object.entries(labels).filter(([key])=>activeChart?.metrics[key]?.value_percent!=null).map(([key,label])=>{const metric=activeChart?.metrics[key];return <article key={key}><strong>{label}</strong><p>{metric?.value_percent==null?'暂无':`${metric.value_percent}%`}</p><small>{metric?.reason?reasons[metric.reason] || '资料不足':key==='volatility'?'按每年252个交易日折算的日收益波动':key==='max_drawdown'?'窗口内峰值到谷值的最大跌幅':'末值相对首值的变化'}</small></article>;})}</div><p className="hint">{activeChart.notice}</p>{activeChart.quality_flags.length>0&&<p>本窗口数据质量待核对。</p>}</>}
      {detail.source && <p>来源：{typeof detail.source==='string'?<a href={detail.source} target="_blank" rel="noreferrer">原始资料</a>:detail.source.url?<a href={detail.source.url} target="_blank" rel="noreferrer">{detail.source.title || '官方规则参考资料'}</a>:'来源信息待核对'}</p>}
      <p className="hint">历史仅在打开详情时按本代码查询，切窗口复用本次结果，不用于成交或选品。数据时点（北京时间）{beijingTime(detail.as_of)}。短历史按实际点展示，历史表现不代表未来回报。</p></>}
  </section></div>;
}
