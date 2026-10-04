import * as w from '../../services/wealth';
export default function Recommendation({plan,products,onDetail}:{plan:w.Plan;products:w.Product[];onDetail:(id:string)=>void}) {
  const budget=plan.request.budget_fen;const items=plan.items.filter(i=>i.command.direction==='purchase');
  const spent=items.reduce((sum,i)=>sum+i.command.amount_fen,0);const cash=Math.max(0,budget-spent);
  const labels:Record<string,string>={cash:'现金管理',bond:'债券',mixed:'混合',index:'指数',equity:'股票'};
  if(plan.stage==='SELL')return <article aria-label="减持建议"><p>当前为减持阶段；赎回到账并重读现金后再生成买入配置。</p>{plan.items.map(item=><div key={item.command.operation_id}><strong>{products.find(p=>p.product_id===item.command.product_id)?.name||item.command.product_id}</strong><p>赎回 {item.command.units} 份 · {item.reason}</p><button className="secondary" onClick={()=>onDetail(item.command.product_id)}>产品详情</button></div>)}</article>;
  return <article aria-label="配置推荐"><h4>推荐配置 · 预算 {w.money(budget)}</h4>{plan.configuration.summary?.map(text=><p key={text}>{text}</p>)}<p className="hint">横向条形占比以本次预算为分母，申购金额包含费用。</p>
    {items.map(item=>{const product=products.find(p=>p.product_id===item.command.product_id);const percent=budget?item.command.amount_fen*100/budget:0;return <div key={item.command.operation_id} className="wealth-allocation-row"><strong>{product?.name || item.command.product_id}</strong><p>{labels[product?.category || ''] || '类别待核对'} · {w.money(item.command.amount_fen)} · {percent.toFixed(2)}%</p><div className="wealth-horizontal-track"><div style={{width:`${percent}%`}}/></div><p>{item.reason}</p><button className="secondary" onClick={()=>onDetail(item.command.product_id)}>产品详情</button></div>;})}
    {cash>0&&<div className="wealth-allocation-row"><strong>规则限制未分配金额</strong><p>{w.money(cash)} · {(budget?cash*100/budget:0).toFixed(2)}%</p><div className="wealth-horizontal-track"><div className="cash" style={{width:`${budget?cash*100/budget:0}%`}}/></div></div>}
  </article>;
}
