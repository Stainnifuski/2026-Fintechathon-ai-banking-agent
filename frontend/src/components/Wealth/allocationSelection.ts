import type { PortfolioPlan, PortfolioSelection } from '../../services/wealth';
import type { ProductSelectionState } from './productSelection';

/** Parse a percentage without binary floating-point money/ratio rounding. */
export function percentageRatio(text: string): string {
  if (!/^\d{1,3}$/.test(text)) throw new Error('整体占比须为0至100的整数百分比');
  const [whole, fraction = ''] = text.split('.');
  const hundredths = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (hundredths > 10000) throw new Error('整体占比不能超过100%');
  return `${Math.floor(hundredths / 10000)}.${String(hundredths % 10000).padStart(4, '0')}`;
}

export function collectPortfolioSelection(plan: PortfolioPlan, state: ProductSelectionState, percentages: Record<string, string>): PortfolioSelection {
  if (state.scopeKey !== plan.asset_scope_id) throw new Error('选择与整体方案不属于同一资产范围');
  const items = state.items.map(({ share }) => ({ product_id: share.product_id, product_version: share.product_version,
    share_class: share.share_class, target_ratio_total: percentageRatio(percentages[share.product_id] ?? '') }));
  return { portfolio_id: plan.portfolio_id, expected_version: plan.version, snapshot_id: plan.snapshot_id, context_id: plan.context_id, items };
}

export function selectionKey(selection: Pick<PortfolioSelection, 'items'>): string {
  return JSON.stringify(selection.items.map(i => [i.product_id, i.product_version, i.share_class, Number(i.target_ratio_total)]).sort((a,b)=>String(a[0]).localeCompare(String(b[0]))));
}

/** Early UI feedback only. The server always repeats every check. */
export function draftIssue(plan:PortfolioPlan,state:ProductSelectionState,percentages:Record<string,string>):string|null {
  let input:PortfolioSelection;
  try{input=collectPortfolioSelection(plan,state,percentages);}catch(e){return (e as Error).message;}
  const targets=new Map((plan.advised_positions??[]).filter(p=>p.account_ids.length).map(p=>[p.product_id,p.target_fen]));
  for(const item of input.items){
    const chosen=state.items.find(r=>r.share.product_id===item.product_id)!;
    const grade=plan.model_view?.snapshot.assessment?.capacity_level;
    if(grade!=null&&(chosen.share.product_risk_level==null||chosen.share.product_risk_level>grade))return '产品风险不符合当前正式评估，零比例也不能绕过';
    const value=Number(item.target_ratio_total)*plan.basis_fen;
    if(!Number.isSafeInteger(value))return '百分比不能表示为整数分';
    targets.set(item.product_id,value);
    const old=plan.advised_positions?.find(p=>p.product_id===item.product_id);
    const increase=value-(old?.current_fen??0);
    const limit=plan.model_view?.adjustment_contract?.limits[item.product_id];
    if(increase>0&&(increase<(limit?.minimum_fen??chosen.share.min_amount_fen??0)||limit?.maximum_fen!=null&&increase>limit.maximum_fen))return '该产品调整金额不符合申购限制';
    if(old?.holding_cap_fen!=null&&value>old.holding_cap_fen)return '该产品只能保留或减持现有份额';
  }
  const sum=[...targets.values()].reduce((a,b)=>a+b,0);
  if(sum>plan.basis_fen)return '产品合计超过全部资产的100%，请自行修改比例';
  const amounts={cash_management:plan.basis_fen-sum,stable:0,growth:0};
  for(const [code,value] of targets){const row=plan.advised_positions?.find(p=>p.product_id===code);const selected=state.items.find(p=>p.share.product_id===code);const bucket=row?.bucket??selected?.candidate.bucket;if(bucket)amounts[bucket]+=value;}
  for(const bucket of plan.buckets){const ratio=amounts[bucket.bucket]/plan.basis_fen;if(ratio<Number(bucket.min_ratio)-1e-10||ratio>Number(bucket.max_ratio)+1e-10)return `${bucket.bucket==='cash_management'?'活钱':bucket.bucket==='stable'?'稳健':'进取'}比例超出原方案允许区间`;}
  const c=plan.model_view?.adjustment_contract;
  if(c){const x=[amounts.cash_management,amounts.stable,amounts.growth].map(v=>v/plan.basis_fen);
    if(c.constraints.some(([a,b,d])=>a*x[0]+b*x[2]>d+1e-10))return '草稿超出当前板块或风险约束';
    const loss=x.reduce((sum,v,i)=>sum+(v-c.b[i])**2+c.eta*(c.h?(v-c.h[i])**2:0),0);
    if(loss>c.loss_limit+1e-10)return '草稿超出原方案U允许范围，请调整比例或明确重新设置偏好';
  }
  return null;
}
