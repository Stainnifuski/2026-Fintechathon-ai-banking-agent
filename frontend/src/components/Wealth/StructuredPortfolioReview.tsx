import AgentStepCard,{WealthStepContext,useStepInstruction} from './AgentStepCard';
import {useEffect,useRef,useState,useContext} from 'react';
import * as w from '../../services/wealth';
import {readPortfolioPlan,readReviewSnapshot} from '../../services/wealthPortfolioReview';
import type {ProductSelectionState} from './productSelection';
import type {ManualDraft} from '../../services/wealthManualDraft';
import PortfolioAllocation from './PortfolioAllocation';
import PlanExecution from './PlanExecution';
import {wealthRequestFailure} from '../../utils/wealthFeedback';

export default function StructuredPortfolioReview({plan:initialPlan,onPrepare,onActivity,onFlowActive,onPrepared}:{onPrepared?:(id:string)=>Promise<void>;plan:w.PortfolioPlan;selection:ProductSelectionState;ratios:ManualDraft['ratios'];onRatiosChange:(draft:ManualDraft['ratios'])=>void;onRevise:(selection:w.PortfolioSelection)=>Promise<w.PortfolioPlan>;onPrepare:(plan:w.PortfolioPlan,paymentAccountId?:string)=>Promise<w.PreparedPortfolio>;onActivity:()=>void;onFlowActive:(active:boolean)=>void}){
  const [plan,setPlan]=useState(initialPlan),[notice,setNotice]=useState('');
  const steps=useContext(WealthStepContext);
  const [continuationPending,setContinuationPending]=useState(false);const continuing=useRef(false);
  const [snapshot,setSnapshot]=useState<w.PortfolioSnapshot|null>(null),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const [prepared,setPrepared]=useState<w.PreparedPortfolio|null>(null),[busy,setBusy]=useState(false);
  const current=JSON.stringify([initialPlan.portfolio_id,initialPlan.version,initialPlan.snapshot_id]);
  useEffect(()=>{let active=true;setPlan(initialPlan);setSnapshot(null);setPrepared(null);setError('');setNotice('');setContinuationPending(false);void w.get<unknown>('/portfolio/snapshot').then(raw=>{const checked=readReviewSnapshot(raw,initialPlan);if(active)setSnapshot(checked);}).catch(e=>{if(active)setError(wealthRequestFailure(e));});return()=>{active=false;};},[current,reload]);
  const missing=(plan.advised_positions??[]).some(p=>p.target_fen!==p.current_fen&&!(plan.reasons.includes('PLANNING_INCREMENTAL_V2')&&p.account_ids.length>0)&&!plan.selected_positions.some(s=>s.product_id===p.product_id&&s.product_version===p.product_version));
  const prepare=async()=>{setBusy(true);setError('');try{const result=await onPrepare(plan);if(result.portfolio_id!==plan.portfolio_id||result.portfolio_version!==plan.version)throw new Error('交易核对结果与当前规划不一致，请重新生成规划。');if(result.execution_plan_id)await onPrepared?.(result.execution_plan_id);setPrepared(result);steps?.complete('allocation');onFlowActive(!!result.execution_plan?.items.length);}catch(e){setError(wealthRequestFailure(e));}finally{setBusy(false);}};
  const continuePurchase=async()=>{
    if(!prepared||continuing.current)return;continuing.current=true;setBusy(true);setError('');setContinuationPending(true);
    try{
      const response=await w.post<{plan:w.PortfolioPlan;prepared:w.PreparedPortfolio}>(`/plans/${prepared.execution_plan_id}/continue-purchase`,{version:prepared.execution_plan_version});
      const next=readPortfolioPlan(response.plan,plan.context_id??'legacy-v1');const snap=readReviewSnapshot(next.model_view?.snapshot,next);
      if(response.prepared.portfolio_id!==next.portfolio_id||response.prepared.portfolio_version!==next.version)throw new Error('申购明细与新规划不一致，请重新核实。');
      if(response.prepared.execution_plan_id)await onPrepared?.(response.prepared.execution_plan_id);
      setPlan(next);setSnapshot(snap);setPrepared(response.prepared);setContinuationPending(false);
      setNotice('赎回实际到账已核实，申购按最新资金与持仓重新计算。请阅读新明细，独立确认并完成第二次MFA。');
      if(!response.prepared.execution_plan?.items.length)onFlowActive(false);
    }catch(e){setError(`${wealthRequestFailure(e)} 已到账资金保留为现金；不会自动追加赎回或提交申购。`);}
    finally{continuing.current=false;setBusy(false);onActivity();}
  };
  useStepInstruction('allocation',()=>{if(!busy&&!missing&&snapshot?.valuation_complete&&!prepared)void prepare();});
  const completed=()=>{if(prepared?.stage==='SELL'){void continuePurchase();return;}setNotice('本次规划交易已完成，资金与持仓以银行结果为准。');steps?.complete('trade');onFlowActive(false);onActivity();};
  return <section aria-label="模型理财规划">
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {!snapshot?<><p role="status">正在读取规划资产资料…</p>{error&&<button onClick={()=>setReload(n=>n+1)}>重试读取</button>}</>:<>
      <AgentStepCard stage="allocation" title="整体资产配置"><PortfolioAllocation plan={plan} snapshot={snapshot} contextId={plan.context_id??'legacy-v1'} modelOnly readOnly onAdjust={async()=>plan} onPreferenceChange={async()=>plan}/>
      {missing&&<p role="status">存在尚未明确选择的持仓调整或产品，请在上方勾选相关产品后继续核对。</p>}
      {!prepared&&<button disabled={busy||missing||!snapshot.valuation_complete} onClick={()=>void prepare()}>{busy?'正在核对交易明细…':'核对差额与准备预览'}</button>}
      </AgentStepCard>{prepared&&<><AgentStepCard stage="details" canContinue title="交易明细"><section aria-label="规划交易明细"><h3>交易明细</h3>{prepared.actions.map(a=><p key={`${a.account_id}:${a.product_id}`}>{a.name||a.product_id}：{w.money(a.current_fen)} → {w.money(a.target_fen)} · {a.direction==='purchase'?'申购':'赎回'}</p>)}
        {!prepared.actions.length&&<p>当前资产已符合目标，无需交易。</p>}
        {prepared.stage==='SELL'&&<p>第一步：确认必要赎回并完成MFA；核实实际到账后，第二步申购需要独立确认与第二次MFA。</p>}
      </section></AgentStepCard>{prepared.execution_plan&&<PlanExecution key={`${prepared.execution_plan_id}:${prepared.execution_plan_version}`} plan={prepared.execution_plan} onActivity={()=>{}} onCompleted={completed}/>}
        {continuationPending&&<button disabled={busy} onClick={()=>void continuePurchase()}>{busy?'正在核实到账并计算申购…':'重试核实到账与准备申购'}</button>}
      </>}
    </>}
  </section>;
}
