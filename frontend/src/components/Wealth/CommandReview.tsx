import {useState} from 'react';
import {money} from '../../services/wealth';
import {adjustmentNote} from '../../services/wealthOperatorReview';
import type {ReviewCommand} from '../../services/wealthOperatorReview';
import {tradeStatusLabel} from './tradeStatus';

/** Shared factual order display; an optional adjustment only prepares a note. */
export default function CommandReview({rows,onAdjustment}:{rows:ReviewCommand[];onAdjustment?:(note:string)=>void}){
  const [values,setValues]=useState<Record<string,string>>({}),[error,setError]=useState('');
  return <section aria-label="原操作逐笔核对"><h4>原操作逐笔核对（{rows.length}笔）</h4>
    {!rows.length&&<p>当前任务没有已保存的交易动作，不能补造订单。</p>}
    {rows.map(({command:c,status})=><article className="wealth-product" key={c.operation_id}>
      <strong>{c.direction==='purchase'?'产品申购':'持仓赎回'}</strong>
      <p>{c.direction==='purchase'?`申购 ${money(c.amount_fen)}`:`赎回 ${c.units}份`} · {tradeStatusLabel(status)}</p>
      {onAdjustment&&<label>待本人核对的{c.direction==='purchase'?'申购金额（元）':'赎回份额'}<input aria-label="拟调整数量" inputMode="decimal" value={values[c.operation_id]??''} onChange={e=>{setValues(v=>({...v,[c.operation_id]:e.target.value}));setError('');}}/></label>}
    </article>)}
    {onAdjustment&&rows.length>0&&<><p>这里只整理调整说明，不修改方案、不生成预览。交还后由本人重新校验、预览和授权。</p><button className="secondary" onClick={()=>{try{onAdjustment(adjustmentNote(rows,values));setError('');}catch(e){setError((e as Error).message);}}}>整理为处理说明</button></>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
