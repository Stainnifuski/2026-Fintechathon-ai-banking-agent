import { useEffect, useState } from 'react';
import * as w from '../../services/wealth';
import type { ProductSelectionState } from './productSelection';
import { collectPortfolioSelection, selectionKey } from './allocationSelection';
import PortfolioAllocation from './PortfolioAllocation';
import PlanExecution from './PlanExecution';
import './allocationReview.css';

const percentage = (ratio:string) => String(Number((Number(ratio)*100).toFixed(2)));
const errorMessage = (error:unknown) => (error as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message || (error instanceof Error ? error.message : '操作失败，请保留当前方案核对');
interface Props {
  plan: w.PortfolioPlan; snapshot: w.PortfolioSnapshot; selection: ProductSelectionState;
  onRevise?: (selection:w.PortfolioSelection) => Promise<w.PortfolioPlan>;
  onPrepare?: (plan:w.PortfolioPlan) => Promise<w.PreparedPortfolio>;
  onActivity?: () => void;
  fixtureOnly?: boolean;
  draftPercentages?:Record<string,string>;
  onPercentagesChange?:(values:Record<string,string>)=>void;
  onPropose?:(selection:w.PortfolioSelection)=>void;
}

/** Same controlled consumer view can be mounted in customer or handoff panels. */
export default function AllocationReview({plan:initial,snapshot,selection,onRevise=w.revisePortfolio,onPrepare=w.preparePortfolio,onActivity=()=>{},fixtureOnly=false,draftPercentages,onPercentagesChange,onPropose}:Props) {
  const [plan,setPlan]=useState(initial);
  const [localPercentages,setPercentages]=useState<Record<string,string>>(()=>Object.fromEntries(selection.items.map(({share})=>[
    share.product_id,percentage(initial.selected_positions.find(p=>p.product_id===share.product_id)?.target_ratio_total ?? '0')])));
  const percentages=draftPercentages??localPercentages;
  const [validatedKey,setValidatedKey]=useState<string|null>(null);
  const [prepared,setPrepared]=useState<w.PreparedPortfolio|null>(null);
  const [busy,setBusy]=useState(false); const [notice,setNotice]=useState('');
  let draft:w.PortfolioSelection|null=null;
  try { draft=collectPortfolioSelection(plan,selection,percentages); } catch { /* Explain invalid input when submitted. */ }
  const clean=!!draft && validatedKey===selectionKey(draft);
  const act=async(work:()=>Promise<void>)=>{setBusy(true);setNotice('');try{await work();}catch(e){setNotice(errorMessage(e));}finally{setBusy(false);}};
  useEffect(()=>{setPrepared(null);setValidatedKey(null);},[draftPercentages]);
  const change=(id:string,value:string)=>{const next={...percentages,[id]:value};setPercentages(next);onPercentagesChange?.(next);setPrepared(null);setValidatedKey(null);};
  return <section className="allocation-review" aria-label="整体配置核对">
    <div className="consumer-card-head"><h2>核对整体配置</h2><span>整体目标 v{plan.version}</span></div>
    <p>以全部资产 {w.money(snapshot.basis_fen)} 为分母。下方比例表示占全部资产的份额；已有持仓、保留现金和应收也必须计入。</p>
    <p className="consumer-note">{onPropose?'当前是原方案及准备时的历史快照。比例修改只形成待本人核对的建议，未通过配置服务校验，不能授权或下单。':fixtureOnly?'当前为录制样例，比例修订只回放已登记的契约结果；不会提交模拟银行。':'修改后先由配置服务校验，再重读资产计算真正需要执行的差额。'}</p>
    {onPropose&&<p className="consumer-note">原方案时点 {plan.as_of}。目标金额和板块汇总仍显示原方案，尚未按建议重算。</p>}
    <div className="allocation-table"><table><thead><tr><th>具体产品／份额</th><th>{onPropose?'建议整体占比':'整体占比'}</th><th>{onPropose?'原方案目标':'已校验目标'}</th><th>{onPropose?'原快照持仓':'现有持仓'}</th></tr></thead><tbody>
      {selection.items.map(({candidate,share})=>{const target=plan.selected_positions.find(p=>p.product_id===share.product_id&&p.product_version===share.product_version);
        const current=snapshot.positions.filter(p=>p.product_id===share.product_id&&p.product_version===share.product_version).reduce((sum,p)=>sum+(p.market_value_fen??0),0);
        return <tr key={share.product_id}><td>{candidate.name}{share.share_class && ` · ${share.share_class}类`}<small>{share.product_id} · 产品 v{share.product_version} · 披露 v{share.disclosure_version}</small></td>
          <td><label><span className="allocation-input-label">{candidate.name}整体占比</span><input aria-label={`${share.product_id}整体占比`} type="number" min="0" max="100" step="0.01" value={percentages[share.product_id]??''} onChange={e=>change(share.product_id,e.target.value)} disabled={busy}/> %</label></td>
          <td>{target?w.money(target.target_fen):'待配置服务校验'}</td><td>{snapshot.valuation_complete?w.money(current):'缺少完整估值'}</td></tr>;})}
    </tbody></table></div>
    {onPropose?<button disabled={busy||!selection.items.length} onClick={()=>act(async()=>{onPropose(collectPortfolioSelection(plan,selection,percentages));setNotice('已整理比例调整建议，未修改方案。交还后由本人重新校验。');})}>整理比例调整建议</button>:<button disabled={busy||!selection.items.length} onClick={()=>act(async()=>{
      const input=collectPortfolioSelection(plan,selection,percentages);
      const revised=await onRevise(input);
      if(revised.portfolio_id!==plan.portfolio_id || revised.snapshot_id!==snapshot.snapshot_id || revised.asset_scope_id!==snapshot.asset_scope_id || revised.context_id!==plan.context_id || revised.version<plan.version) throw new Error('配置服务返回了旧版本、不同范围或快照，请重新读取方案');
      setPlan(revised);setValidatedKey(selectionKey({items:revised.selected_positions}));setPrepared(null);
      setNotice('已收到配置服务校验结果，请核对板块与目标后再准备差额。');
    })}>校验所选产品与比例</button>}
    <PortfolioAllocation plan={plan} snapshot={snapshot} contextId={plan.context_id??'legacy-v1'} showProductEditor={false} loading={busy} readOnly={!!onPropose||fixtureOnly}
      onAdjust={onRevise} onPreferenceChange={onRevise} onAccepted={next=>{setPlan(next);setPrepared(null);setValidatedKey(selectionKey({items:next.selected_positions}));}}/>
    {plan.selected_positions.length===0&&plan.candidate_groups.every(group=>group.items.length===0)&&<p role="note">当前没有合格产品候选。此图仅显示账户现金及受候选不足限制的配置基准，尚未完成可投资产品配置；未发生申购。</p>}
    <p>已校验保留现金 {w.money(plan.cash_residual_fen)} · 赎回应收 {w.money(snapshot.receivable_fen)}（到账前不可用于买入）</p>
    <details><summary>查看全部现有持仓与保留项目（{snapshot.positions.length}）</summary>{snapshot.positions.length?snapshot.positions.map(position=><p key={position.position_key}>{position.account_id} · {position.product_id} v{position.product_version} · {position.units}份 · 市值 {w.money(position.market_value_fen)} · 可赎 {position.redeemable_units}份</p>):<p>当前快照没有持仓；不会把在途申请算成已确认份额。</p>}</details>
    {!onPropose&&<><button disabled={busy||!clean||!snapshot.valuation_complete} onClick={()=>act(async()=>{
      const result=await onPrepare(plan);
      if(result.portfolio_id!==plan.portfolio_id||result.portfolio_version!==plan.version)throw new Error('差额结果与当前整体版本不一致');
      setPrepared(result);
    })}>核对差额与准备预览</button>
    {!clean&&<p className="consumer-note">先点击“校验所选产品与比例”。修改比例后需再次校验，旧预览随之失效。</p>}
    </>}
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
    {!onPropose&&prepared&&<section aria-label="整体目标差额"><h3>真正需要执行的动作</h3><p>准备动作不冻结、不自动申购或赎回。{prepared.stage==='SELL'&&'当前先减持；后续买入等待实际到账及重读现金，重新确认与验证。'}</p>
      {prepared.actions.length?<div className="allocation-table"><table><thead><tr><th>产品</th><th>现有／目标</th><th>动作</th><th>状态</th></tr></thead><tbody>{prepared.actions.map(a=><tr key={`${a.account_id}:${a.product_id}`}><td>{a.name||a.product_id}<small>{a.account_id} · v{a.product_version}</small></td><td>{w.money(a.current_fen)} → {w.money(a.target_fen)}</td><td>{a.direction==='purchase'?`申购 ${w.money(a.amount_fen)}`:`赎回 ${a.units}份（估算市值 ${w.money(-a.difference_fen)}）`}</td><td>{a.status==='WAITING_SETTLEMENT_AND_REREAD_CASH'?'等待赎回到账及重读现金':'等待预览与本人授权'}</td></tr>)}</tbody></table></div>:<p>整体目标与现有持仓一致，无需交易授权。</p>}
      <p className="consumer-note">整体版本 {prepared.portfolio_version} · 执行版本 {prepared.execution_plan_version??'样例未建立银行执行记录'}</p>
      {fixtureOnly?<p className="consumer-warning">这里只展示 F11 差额，样例不产生订单。七笔真实模拟银行的确认、验证码和冻结已通过隔离数据库测试。</p>:prepared.execution_plan&&<PlanExecution key={`${prepared.execution_plan_id}:${prepared.execution_plan_version}`} plan={prepared.execution_plan} onActivity={onActivity}/>}
    </section>}
  </section>;
}
