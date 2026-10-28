import type {AssetSummary} from '../../services/wealth';
import {beijingTime} from '../../utils/time';
import {summaryMoney,summaryTotal} from './view.ts';
import './assetSummary.css';

interface Props {data:AssetSummary|null;loading?:boolean;error?:string;onRefresh?:()=>void;variant?:'hero'|'page'|'fixture';label?:string}
export default function AssetSummaryCard({data,loading=false,error='',onRefresh,variant='page',label='总资产'}:Props){
  return <section className={`asset-summary-card ${variant==='hero'?'safe-card':variant==='fixture'?'consumer-card':'card'} asset-summary-${variant}`} aria-label={variant==='fixture'?'示例资产概况':'银行总资产'}
      data-asset-scope-id={data?.asset_scope_id} data-snapshot-id={data?.snapshot_id}>
    <div className="asset-summary-heading"><h2>{label}</h2>{onRefresh&&error&&<button className="secondary" disabled={loading} onClick={onRefresh}>{loading?'正在重试…':'重试'}</button>}</div>
    <strong className="asset-summary-total">{summaryTotal(data,error)}</strong>
    {loading&&!data&&<p role="status">正在读取银行资产…</p>}
    {error&&<p role="alert">{error}</p>}
    {data&&<><p className="asset-summary-source">{variant==='fixture'?'虚构契约样例，与登录账户资金无关':`当前授权范围 · ${data.account_ids.length}个借记账户 · 模拟银行事实`}</p>
      <dl className="asset-summary-metrics" data-has-receivable={data.redemption_receivable_fen > 0}>
        <div><dt>账面现金</dt><dd>{summaryMoney(data.cash_balance_fen)}</dd></div>
        <div><dt>可用现金</dt><dd>{summaryMoney(data.available_cash_fen)}</dd></div>
        <div><dt>持仓市值</dt><dd>{summaryMoney(data.positions_market_value_fen)}</dd></div>
        {data.redemption_receivable_fen > 0 && <div><dt>赎回应收</dt><dd>{summaryMoney(data.redemption_receivable_fen)}</dd></div>}
      </dl>
      <p>其中冻结 {summaryMoney(data.held_fen)}，已包含在账面现金中。{data.redemption_receivable_fen > 0 && '历史赎回应收到账前不可用于买入。'}</p>
      {!data.valuation_complete&&<p className="asset-summary-warning">持仓估值不完整，当前无法计算完整总资产。已知资产保留显示，缺失估值不按0计算。</p>}
      <small>查询 {beijingTime(data.as_of)} · 业务时间 {beijingTime(data.business_at)}</small>
    </>}
  </section>;
}
