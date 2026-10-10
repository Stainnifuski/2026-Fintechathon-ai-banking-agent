import {useEffect,useState} from 'react';
import * as w from '../../services/wealth';
import {readReviewSnapshot} from '../../services/wealthPortfolioReview';
import type {ProductSelectionState} from './productSelection';
import AllocationReview from './AllocationReview';

export default function StructuredPortfolioReview({plan,selection,onRevise,onPrepare,onActivity}:{plan:w.PortfolioPlan;selection:ProductSelectionState;onRevise:(selection:w.PortfolioSelection)=>Promise<w.PortfolioPlan>;onPrepare:(plan:w.PortfolioPlan)=>Promise<w.PreparedPortfolio>;onActivity:()=>void}){
  const [snapshot,setSnapshot]=useState<w.PortfolioSnapshot|null>(null),[error,setError]=useState(''),[reload,setReload]=useState(0);
  useEffect(()=>{let active=true;setSnapshot(null);setError('');void w.get<unknown>('/portfolio/snapshot').then(raw=>{const checked=readReviewSnapshot(raw,plan);if(active)setSnapshot(checked);}).catch(e=>{if(active)setError(e?.response?.data?.detail?.message??(e instanceof Error?e.message:'资产快照读取失败，请重新查询方案。'));});return()=>{active=false;};},[plan,reload]);
  return <section aria-label="统一整体方案核对"><h3>整体方案 · v{plan.version}</h3><p>基准 {w.money(plan.basis_fen)} · 保留现金 {w.money(plan.cash_residual_fen)} · 服务 {plan.provider_id} / {plan.model_status}</p><p>只有与该方案绑定的资产快照才能用于比例与差额核对；勾选不会下单。</p>{plan.missing_fields.length>0&&<p className="consumer-warning">资料缺失：{plan.missing_fields.join('、')}</p>}
    {error?<><p role="alert">{error}</p><button className="secondary" onClick={()=>setReload(n=>n+1)}>重新读取方案快照</button></>:!snapshot?<p role="status">正在核对方案的资产快照…</p>:<AllocationReview key={`${plan.portfolio_id}:${plan.version}:${snapshot.snapshot_id}:${selection.items.map(i=>`${i.share.product_id}:${i.share.product_version}`).join(',')}`} plan={plan} snapshot={snapshot} selection={{...selection,scopeKey:plan.asset_scope_id}} onRevise={onRevise} onPrepare={onPrepare} onActivity={onActivity}/>}
  </section>;
}
