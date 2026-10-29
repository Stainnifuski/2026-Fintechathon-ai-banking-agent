import AgentStepCard,{WealthStepContext,useStepInstruction} from './AgentStepCard';
import {useEffect,useRef,useState,useContext} from 'react';
import * as w from '../../services/wealth';
import type {ProductSelectionState} from './productSelection';
import PlanExecution from './PlanExecution';
import {wealthRequestFailure} from '../../utils/wealthFeedback';
import {targetAmountState} from './targetAmounts';

/** Explicit per-product amounts grouped for execution; no allocation portfolio. */
export default function TargetPurchase({selection,assets,accountId,onActivity,onPrepared,initialAmounts,initialAmountsKey,novaTaskId,autoPrepare=false}:{autoPrepare?:boolean;selection:ProductSelectionState;assets:w.Assets;accountId:string;onActivity:()=>void;novaTaskId?:string;onPrepared?:(id:string)=>Promise<void>;initialAmounts?:Record<string,string>;initialAmountsKey?:string}){
  const stepContext=useContext(WealthStepContext);
  const [amounts,setAmounts]=useState<Record<string,string>>(()=>initialAmounts??{});
  useEffect(()=>{stepContext?.draft?.({amounts_yuan:amounts});},[amounts,stepContext?.draft]);
  const account=assets.cash.find(a=>a.account_id===accountId&&['savings','checking'].includes(a.account_type));
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [result,setResult]=useState<{plan:w.Plan;preview:w.PlanPreview|null}|null>(null);
  const requestId=useRef<string|null>(null);
  const scope=assets.context_id??'legacy-v1';
  const selectionKey=JSON.stringify(selection.items.map(({share})=>[share.product_id,share.product_version,share.disclosure_version]));
  const scopeKey=JSON.stringify([scope,accountId,selectionKey]);
  const currentScope=useRef(scopeKey);currentScope.current=scopeKey;
  const amountState=targetAmountState(selection.items.map(({share})=>share.product_id),amounts);
  useEffect(()=>{setAmounts(initialAmounts??{});setResult(null);setError('');requestId.current=null;},[scope,accountId,initialAmountsKey]);
  useEffect(()=>{setResult(null);setError('');requestId.current=null;},[selectionKey]);
  useEffect(()=>{
    if(!result&&!amountState.valid&&stepContext?.completed.includes('amounts'))
      stepContext.invalidate?.('amounts','purchase_review','trade');
  },[result,amountState.valid,stepContext?.completed,stepContext?.invalidate]);
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
      // The server checks current cash; the page's asset snapshot may be older.
      requestId.current??='WP'+crypto.randomUUID().replace(/-/g,'');
      const prepared=await w.post<{plan:w.Plan;preview:w.PlanPreview|null}>('/target-purchases/prepare',{
        request_id:requestId.current,context_id:scope,items,
      },novaTaskId);
      if(currentScope.current!==scopeKey)throw new Error('已选产品或账户范围已变化，请重新核对全部申购。');
      await onPrepared?.(prepared.plan.plan_id);
      setResult(prepared);
      stepContext?.complete('amounts','purchase_review');
    }catch(reason){setError(wealthRequestFailure(reason));}finally{setBusy(false);}
  };
  useStepInstruction('purchase_review',()=>{if(!busy&&!result)void prepare();});
  useStepInstruction('amounts',()=>{if(!busy&&!result)void prepare();});
  const autoAttempted=useRef(false);
  useEffect(()=>{if(autoPrepare&&!autoAttempted.current&&selection.items.length&&selection.items.every(({share})=>Number(amounts[share.product_id])>0)){
    autoAttempted.current=true;void prepare();
  }},[autoPrepare,selectionKey,amounts]);
  if(!selection.items.length)return null;
  return <>{error&&<p role="alert">{error}</p>}<AgentStepCard stage="amounts" canContinue={!busy&&amountState.valid} title="填写申购金额"><section aria-label="按所选产品金额申购"><h3>填写所选产品的申购金额</h3>
    <p>每只产品由本人指定金额，全部已选产品统一核对、一次本人确认与验证码后申购。不计算资产配置比例。</p>
    <p>{account?`使用绑定的借记账户 · 可用 ${w.money(account.available_fen)}`:'当前理财绑定的借记账户不可用，请刷新账户资料。'}</p>
    {selection.items.map(({candidate,share})=><div key={`${share.product_id}:${share.product_version}`}><label>{candidate.name}申购金额（元）<input inputMode="decimal" disabled={busy||!!result} value={amounts[share.product_id]??''} onChange={e=>{requestId.current=null;setAmounts(current=>({...current,[share.product_id]:e.target.value}));}}/></label></div>)}
    </section></AgentStepCard><AgentStepCard stage="purchase_review" title="申购合计与核对"><section><p>已填写申购金额合计 {w.money(amountState.totalFen)}</p>
    {!result&&!amountState.valid&&<p role="status">请先为每只已选产品填写有效的正数申购金额。</p>}
    {!result&&<button disabled={busy||!account||!amountState.valid} onClick={()=>void prepare()}>{busy?'正在核对全部申购…':`统一申购全部已选（${selection.items.length}只）`}</button>}
    {error&&<p role="alert">{error}</p>}
  </section></AgentStepCard>{result&&<PlanExecution key={result.plan.plan_id} plan={result.plan} initialPreview={result.preview} onActivity={onActivity}/>}</>;
}
