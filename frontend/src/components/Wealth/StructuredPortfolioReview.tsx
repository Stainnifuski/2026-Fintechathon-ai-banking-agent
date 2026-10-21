import {useEffect,useState} from 'react';
import * as w from '../../services/wealth';
import {readReviewSnapshot} from '../../services/wealthPortfolioReview';
import type {ProductSelectionState} from './productSelection';
import type {ManualDraft} from '../../services/wealthManualDraft';
import PortfolioAllocation from './PortfolioAllocation';
import PlanExecution from './PlanExecution';
import {wealthRequestFailure} from '../../utils/wealthFeedback';

export default function StructuredPortfolioReview({plan,onPrepare,onActivity}:{plan:w.PortfolioPlan;selection:ProductSelectionState;ratios:ManualDraft['ratios'];onRatiosChange:(draft:ManualDraft['ratios'])=>void;onRevise:(selection:w.PortfolioSelection)=>Promise<w.PortfolioPlan>;onPrepare:(plan:w.PortfolioPlan,paymentAccountId?:string)=>Promise<w.PreparedPortfolio>;onActivity:()=>void}){
  const [snapshot,setSnapshot]=useState<w.PortfolioSnapshot|null>(null),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const [prepared,setPrepared]=useState<w.PreparedPortfolio|null>(null),[busy,setBusy]=useState(false);
  const current=JSON.stringify([plan.portfolio_id,plan.version,plan.snapshot_id]);
  useEffect(()=>{let active=true;setSnapshot(null);setPrepared(null);setError('');void w.get<unknown>('/portfolio/snapshot').then(raw=>{const checked=readReviewSnapshot(raw,plan);if(active)setSnapshot(checked);}).catch(e=>{if(active)setError(wealthRequestFailure(e));});return()=>{active=false;};},[current,reload]);
  const missing=(plan.advised_positions??[]).some(p=>p.target_fen!==p.current_fen&&!(plan.reasons.includes('PLANNING_CASH_BUDGET_V1')&&p.account_ids.length>0)&&!plan.selected_positions.some(s=>s.product_id===p.product_id&&s.product_version===p.product_version));
  const prepare=async()=>{setBusy(true);setError('');try{const result=await onPrepare(plan);if(result.portfolio_id!==plan.portfolio_id||result.portfolio_version!==plan.version)throw new Error('交易核对结果与当前规划不一致，请重新生成规划。');setPrepared(result);}catch(e){setError(wealthRequestFailure(e));}finally{setBusy(false);}};
  return <section aria-label="模型理财规划">
    {error&&<p role="alert">{error}</p>}
    {!snapshot?<><p role="status">正在读取规划资产资料…</p>{error&&<button onClick={()=>setReload(n=>n+1)}>重试读取</button>}</>:<>
      <PortfolioAllocation plan={plan} snapshot={snapshot} contextId={plan.context_id??'legacy-v1'} modelOnly readOnly onAdjust={async()=>plan} onPreferenceChange={async()=>plan}/>
      {missing&&<p role="status">存在尚未明确选择的持仓调整或产品，请在上方勾选相关产品后继续核对。</p>}
      <button disabled={busy||missing||!snapshot.valuation_complete} onClick={()=>void prepare()}>{busy?'正在核对交易明细…':'核对差额与准备预览'}</button>
      {prepared&&<section aria-label="规划交易明细"><h3>交易明细</h3>{prepared.actions.map(a=><p key={`${a.account_id}:${a.product_id}`}>{a.name||a.product_id}：{w.money(a.current_fen)} → {w.money(a.target_fen)} · {a.direction==='purchase'?'申购':'赎回'}</p>)}
        {!prepared.actions.length&&<p>当前资产已符合目标，无需交易。</p>}
        {prepared.execution_plan&&<PlanExecution key={`${prepared.execution_plan_id}:${prepared.execution_plan_version}`} plan={prepared.execution_plan} onActivity={onActivity}/>}
      </section>}
    </>}
  </section>;
}