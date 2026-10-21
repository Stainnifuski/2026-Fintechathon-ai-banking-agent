import {money} from '../../services/wealth';
import {planningCash,type CashDraft} from './planningCash';
export default function PlanningCashInput({available,draft,onChange}:{available:number;draft:CashDraft;onChange:(draft:CashDraft)=>void}){
  const budget=planningCash(available,draft);
  const value=(field:CashDraft['field'])=>draft.field===field?draft.text:budget?((field==='reserve'?budget.reserve:budget.investment)/100).toFixed(2):'';
  return <section aria-label="本次规划资金"><p><strong>当前可用现金 {money(available)}</strong></p>
    <label>保留现金金额（元）<input aria-label="保留现金金额" inputMode="decimal" value={value('reserve')} onChange={e=>onChange({field:'reserve',text:e.target.value})}/></label>
    <label>本次投资金额（元）<input aria-label="本次投资金额" inputMode="decimal" value={value('investment')} onChange={e=>onChange({field:'investment',text:e.target.value})}/></label>
    <p>任选一项填写，另一项自动计算，两者合计等于当前可用现金。现金计入活钱，稳健与进取比例由模型推荐。</p>
    {!budget&&<p role="status">请输入0至当前可用现金之间的金额，最多两位小数。</p>}
  </section>;
}
