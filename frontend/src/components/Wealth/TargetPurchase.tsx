import {useEffect,useRef,useState} from 'react';
import * as w from '../../services/wealth';
import type {ProductSelectionState} from './productSelection';
import PlanExecution from './PlanExecution';
import {wealthRequestFailure} from '../../utils/wealthFeedback';

/** Explicit per-product amounts grouped for execution; no allocation portfolio. */
export default function TargetPurchase({selection,assets,accountId,onActivity}:{selection:ProductSelectionState;assets:w.Assets;accountId:string;onActivity:()=>void}){
  const [amounts,setAmounts]=useState<Record<string,string>>({});
  const account=assets.cash.find(a=>a.account_id===accountId&&['savings','checking'].includes(a.account_type));
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [result,setResult]=useState<{plan:w.Plan;preview:w.PlanPreview|null}|null>(null);
  const requestId=useRef<string|null>(null);
  const scope=assets.context_id??'legacy-v1';
  const selectionKey=JSON.stringify(selection.items.map(({share})=>[share.product_id,share.product_version,share.disclosure_version]));
  const scopeKey=JSON.stringify([scope,accountId,selectionKey]);
  const currentScope=useRef(scopeKey);currentScope.current=scopeKey;
  const totalFen=selection.items.reduce((sum,{share})=>{
    const amount=amounts[share.product_id]??'';
    if(!/^\d+(\.\d{1,2})?$/.test(amount))return sum;
    const [whole,fraction='']=amount.split('.');return sum+BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
  },0n);
  useEffect(()=>{setAmounts({});setResult(null);setError('');requestId.current=null;},[scope,accountId]);
  useEffect(()=>{setResult(null);setError('');requestId.current=null;},[selectionKey]);
  const prepare=async()=>{
    setError('');
    if(!account){setError('当前理财绑定的借记账户不可用，请刷新账户资料。');return;}
    setBusy(true);
    try{
      const items=selection.items.map(({candidate,share})=>{
        const amount=amounts[share.product_id]??'';
        if(!/^\d+(\.\d{1,2})?$/.test(amount))throw new Error(`${candidate.name}：请输入申购金额，最多两位小数。`);
        const [whole,fraction='']=amount.split('.');const fen=BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
        if(fen<=0n||fen>2000000000n)throw new Error(`${candidate.name}：金额需大于零且不超过业务上限。`);
        return {product_id:share.product_id,product_version:share.product_version,disclosure_version:share.disclosure_version,amount_fen:Number(fen)};
      });
      if(items.reduce((sum,item)=>sum+item.amount_fen,0)>account.available_fen)throw new Error('全部已选产品的申购总额超过借记账户可用现金。');
      requestId.current??='WP'+crypto.randomUUID().replace(/-/g,'');
      const prepared=await w.post<{plan:w.Plan;preview:w.PlanPreview|null}>('/target-purchases/prepare',{
        request_id:requestId.current,context_id:scope,items,
      });
      if(currentScope.current!==scopeKey)throw new Error('已选产品或账户范围已变化，请重新核对全部申购。');
      setResult(prepared);
    }catch(reason){setError(wealthRequestFailure(reason));}finally{setBusy(false);}
  };
  if(!selection.items.length)return null;
  return <section aria-label="按所选产品金额申购"><h3>填写所选产品的申购金额</h3>
    <p>每只产品由本人指定金额，全部已选产品统一核对、一次本人确认与验证码后申购。不计算资产配置比例。</p>
    <p>{account?`使用绑定的借记账户 · 可用 ${w.money(account.available_fen)}`:'当前理财绑定的借记账户不可用，请刷新账户资料。'}</p>
    {selection.items.map(({candidate,share})=><div key={`${share.product_id}:${share.product_version}`}><label>{candidate.name}申购金额（元）<input inputMode="decimal" disabled={busy||!!result} value={amounts[share.product_id]??''} onChange={e=>{requestId.current=null;setAmounts(current=>({...current,[share.product_id]:e.target.value}));}}/></label></div>)}
    <p>已填写申购金额合计 {w.money(Number(totalFen))}</p>
    {!result&&<button disabled={busy||!account||selection.items.some(({share})=>!amounts[share.product_id])} onClick={()=>void prepare()}>{busy?'正在核对全部申购…':`统一申购全部已选（${selection.items.length}只）`}</button>}
    {error&&<p role="alert">{error}</p>}{result&&<PlanExecution key={result.plan.plan_id} plan={result.plan} initialPreview={result.preview} onActivity={onActivity}/>}
  </section>;
}
