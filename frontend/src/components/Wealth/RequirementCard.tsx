import { money } from '../../services/wealth';
import { beijingTime } from '../../utils/time';

export interface RequirementState {
  version: number;
  bank_cash: {available_fen:number;account_id:string;as_of:string} | null;
  fields: Record<string, {state:string; value?:unknown; resolved_fen?:number; resolved_days?:number; base_fen?:number; quote?:string}>;
}

export default function RequirementCard({state}: {state:RequirementState}) {
  const labels: Record<string,string> = {investment_budget:'投入预算',declared_assets:'自述资产（非银行余额）',
    reserve_requirement:'投资后最低保留现金',investment_goal:'投资目标',horizon:'可投资期限',liquidity_requirement:'临时用钱到账要求',spending_purpose:'具体支出用途',preferences:'偏好',excluded_categories:'排除类别'};
  const states: Record<string,string> = {unmentioned:'未提及',unknown:'不确定',skipped:'已跳过',
    no_extra_requirement:'不要求额外留现金',no_specific_purpose:'无特别用途',base_pending:'等待核对比例基数'};
  const categories: Record<string,string> = {cash:'现金管理',bond:'债券',mixed:'混合',equity:'股票',index:'指数'};
  function display(key:string) {
    const field=state.fields[key];
    if (!field) return '未提及';
    if (states[field.state]) return states[field.state];
    if (key==='investment_budget') {
      const budget=field.value as {mode:string;ratio?:string;base?:string;min_fen?:number;max_fen?:number};
      if (budget.mode==='proportion') return `${Number(budget.ratio)*100}% × ${money(field.base_fen)} = ${money(field.resolved_fen)}（向下取整到分）`;
      if (budget.mode==='range') return `${money(budget.min_fen)}—${money(budget.max_fen)}（待选择）`;
      if (budget.mode==='request_suggestion') return '请求建议，尚未接受金额';
      return money(field.resolved_fen);
    }
    if (key==='horizon') return (field.value as {text?:string})?.text || '不确定';
    if (key==='investment_goal') return ({reduce_volatility:'尽量减少波动',growth:'争取增长',balanced:'兼顾波动与增长',preserve_value:'保值目标（不代表保本）'} as Record<string,string>)[String(field.value)] || '未提及';
    if (key==='liquidity_requirement') {
      const value=field.value as {mode:string;max_business_days?:number};
      return value.mode==='immediate'?'立即到账':value.mode==='few_business_days'?`可等待${value.max_business_days ?? '几个'}工作日`:'灵活';
    }
    if (key==='declared_assets' || key==='reserve_requirement') return money(field.value as number);
    if (Array.isArray(field.value)) return field.value.map(value=>categories[String(value)] || String(value)).join('、') || '未指定';
    return String(field.value ?? '未提及');
  }
  return <article className="wealth-product" aria-label="当前理财需求">
    <h4>当前理财需求 · v{state.version}</h4>
    {state.bank_cash && <p>银行可用现金 {money(state.bank_cash.available_fen)}<small> · 业务时点（北京时间）{beijingTime(state.bank_cash.as_of)}</small></p>}
    <dl>{Object.entries(labels).map(([key,label])=><div key={key}><dt>{label}</dt><dd>{display(key)}</dd></div>)}</dl>
    <p className="hint">可直接回复“改八千”“不要混合”等修订条件；需求卡不代表本人交易确认或授权。</p>
  </article>;
}
