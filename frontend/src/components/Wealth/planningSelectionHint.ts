import type {PortfolioPlan} from '../../types/wealthPortfolio';
import {incrementalBudget} from './incrementalBudget.ts';

type Bucket='cash_management'|'stable'|'growth';
const order:Bucket[]=['cash_management','stable','growth'];
export const planningPoolNames:Record<Bucket,string>={cash_management:'活钱候选池',stable:'稳健候选池',growth:'进取候选池'};

/** Display guidance from the server's baseline plan, never trade permission. */
export function planningSelectionHint(plan:PortfolioPlan|null,reserve:number|null,selected:Bucket[]){
  if(!plan)return {required:[] as Bucket[],missing:[] as Bucket[],description:'请先生成理财规划，系统会在这里列出需要选择的候选池。',warning:''};
  const snapshot=plan.model_view?.snapshot;
  if(!snapshot||!snapshot.valuation_complete||snapshot.positions.some(p=>p.market_value_fen===null||p.bucket===null)){
    return {required:[] as Bucket[],missing:[] as Bucket[],description:'持仓资料尚未核实，请重新生成理财规划后查看需要选择的候选池。',warning:''};
  }
  const amounts=order.map(bucket=>plan.buckets.find(b=>b.bucket===bucket)!.target_fen);
  const unavailable=snapshot.accounts.reduce((sum,a)=>sum+a.balance_fen-a.available_fen,0)+(snapshot.receivable_fen??0);
  const holdings=order.map(bucket=>snapshot.positions.filter(p=>p.bucket===bucket).reduce((sum,p)=>sum+p.market_value_fen!,0));
  const available=snapshot.accounts.reduce((sum,a)=>sum+a.available_fen,0);
  const reference=plan.planning_reference;
  const budget=incrementalBudget(reference?.level??snapshot.assessment?.capacity_level??4,plan.basis_fen,holdings,reserve??0,available,unavailable,reference?.model_amounts_fen??amounts,reference?.fee_rate??'0');
  if(!budget)return {required:[] as Bucket[],missing:[] as Bucket[],description:'资产或现金安排已变化，请重新核实后查看需要选择的候选池。',warning:'请先核实现金安排与持仓资料。'};
  const required=order.filter((_,i)=>budget.purchases[i]>0);
  const missing=required.filter(bucket=>!selected.includes(bucket));
  const pools=(buckets:Bucket[])=>buckets.map(bucket=>planningPoolNames[bucket]).join('、');
  const description=required.length?`请从${pools(required)}中${required.length>1?'各':''}至少选择一只产品。${reserve===null?'填写现金安排后，需要选择的候选池会自动更新。':required.length<3?'其他候选池已由保留现金或现有持仓覆盖，无需新增选择。':''}`:'当前保留现金和现有持仓已覆盖各板块配置需求，无需新增选择。';
  const warning=missing.length?`请在${pools(missing)}中${missing.length>1?'各':''}至少选择一只产品。`:'';
  return {required,missing,description,warning};
}
