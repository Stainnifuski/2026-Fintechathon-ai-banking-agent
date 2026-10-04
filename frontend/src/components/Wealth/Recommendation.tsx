import * as w from '../../services/wealth';
export default function Recommendation({plan,products,onDetail}:{plan:w.Plan;products:w.Product[];onDetail:(id:string)=>void}) {
  const budget=plan.request.budget_fen;const items=plan.items.filter(i=>i.command.direction==='purchase');
  const spent=items.reduce((sum,i)=>sum+i.command.amount_fen,0);const cash=Math.max(0,budget-spent);
  const labels:Record<string,string>={cash:'现金管理',bond:'债券',mixed:'混合',index:'指数',equity:'股票'};
  if(plan.stage==='SELL')return <p>当前为减持阶段；赎回到账并重读现金后再生成买入配置。</p>;
  return <article aria-label="配置推荐"><h4>推荐配置 · 预算 {w.money(budget)}</h4>{plan.configuration.summary?.map(text=><p key={text}>{text}</p>)}<p className="hint">横向条形占比以本次预算为分母，申购金额包含费用。</p>
    {items.map(item=>{const product=products.find(p=>p.product_id===item.command.product_id);const percent=budget?item.command.amount_fen*100/budget:0;return <div key={item.command.operation_id} className="wealth-allocation-row"><strong>{product?.name || item.command.product_id}</strong><p>{labels[product?.category || ''] || '类别待核对'} · {w.money(item.command.amount_fen)} · {percent.toFixed(2)}%</p><div className="wealth-horizontal-track"><div style={{width:`${percent}%`}}/></div><p>{item.reason}</p><button className="secondary" onClick={()=>onDetail(item.command.product_id)}>产品详情</button></div>;})}
    <div className="wealth-allocation-row"><strong>预算未投入现金</strong><p>{w.money(cash)} · {(budget?cash*100/budget:0).toFixed(2)}%</p><div className="wealth-horizontal-track"><div className="cash" style={{width:`${budget?cash*100/budget:0}%`}}/></div></div>
    <p>当前选项申购费估算 {plan.configuration.estimated_fee_fen == null ? '待重新预览核对' : w.money(plan.configuration.estimated_fee_fen)}（已含在支出）。</p><p>采用后账户可用现金估算 {w.money(plan.configuration.account_remaining_cash_fen)}，不参与预算占比；资金变动后须重新预检。</p></article>;
}
