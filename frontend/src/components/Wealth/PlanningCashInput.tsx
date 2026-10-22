import {money} from '../../services/wealth';
import {planningCash,type CashDraft} from './planningCash';
export default function PlanningCashInput({available,holdings,draft,onChange,disabled=false}:{available:number;holdings:number|null;disabled?:boolean;draft:CashDraft;onChange:(draft:CashDraft)=>void}){
  const budget=holdings===null?null:planningCash(available,draft,holdings);
  const value=(field:CashDraft['field'])=>draft.field===field?draft.text:budget?((field==='reserve'?budget.reserve:field==='investment'?budget.investment:holdings!+budget.investment)/100).toFixed(2):'';
  return <section aria-label="本次规划资金"><p><strong>当前可用现金 {money(available)}</strong> · 当前持仓金额 {holdings===null?'估值未完成':money(holdings)}</p>
    <label>保留现金金额（元）<input aria-label="保留现金金额" disabled={disabled||holdings===null} inputMode="decimal" value={value('reserve')} onChange={e=>onChange({field:'reserve',text:e.target.value})}/></label>
    <label>本次投资金额（元）<input aria-label="本次投资金额" disabled={disabled||holdings===null} inputMode="decimal" value={value('investment')} onChange={e=>onChange({field:'investment',text:e.target.value})}/></label>
    <label>总投资金额（元）<input aria-label="总投资金额" disabled={disabled||holdings===null} inputMode="decimal" value={value('total')} onChange={e=>onChange({field:'total',text:e.target.value})}/></label>
    <p>任选一项填写，其余两项自动计算。保留现金＋本次投资＝当前可用现金；总投资＝当前持仓＋本次投资。本次投资仅指新增现金，旧持仓仅超出硬性上限时建议减持；成交费用与净值变化会影响最终投资市值。</p>
    {!budget&&<p role="status">请填写有效金额，最多两位小数。保留现金和本次投资不超过可用现金，总投资在当前持仓至持仓加可用现金之间。</p>}
  </section>;
}
