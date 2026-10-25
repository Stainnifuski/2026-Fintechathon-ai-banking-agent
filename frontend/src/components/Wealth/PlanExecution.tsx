import AgentStepCard from './AgentStepCard';
import CommandReview from './CommandReview';
import { useEffect, useRef, useState } from 'react';
import * as w from '../../services/wealth';
import type { SecondFactor as Factor } from '../../services/bank';
import { SecondFactor } from '../SecondFactor';
import { beijingTime } from '../../utils/time';
import { disclosureLabels, displayDisclosure, visibleDisclosures } from './disclosureView';
import {wealthRequestFailure} from '../../utils/wealthFeedback';

interface Props { plan:w.Plan; initialPreview?:w.PlanPreview|null; onActivity:()=>void; onCompleted?:(message:string)=>void }
/** Existing plan endpoints and shared second factor; no alternative authority. */
export default function PlanExecution({plan:initial,initialPreview=null,onActivity,onCompleted}:Props){
  const [plan,setPlan]=useState(initial);const [preview,setPreview]=useState<w.PlanPreview|null>(initialPreview);
  const [consent,setConsent]=useState(false);const [stage,setStage]=useState<'PREVIEW'|'CONFIRMED'|'VERIFIED'>('PREVIEW');
  const [factor,setFactor]=useState<Factor|null>({method:'totp'});const [code,setCode]=useState('');
 useEffect(()=>{const clear=()=>{setCode('');setConsent(false);};window.addEventListener('nova-clear-verification',clear);return()=>window.removeEventListener('nova-clear-verification',clear);},[]);
  const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');
  const [reviewFailed,setReviewFailed]=useState(false);
  const firstReview=useRef<Promise<w.PlanPreview>|null>(null);
  useEffect(()=>{
    if(preview||initialPreview||initial.status!=='AWAITING_CONFIRMATION'||!initial.items.length)return;
    let active=true;setBusy(true);
    firstReview.current??=w.post<w.PlanPreview>(`/plans/${initial.plan_id}/preview`,{version:initial.version});
    void firstReview.current.then(value=>{if(active){setPreview(value);setReviewFailed(false);}}).catch(e=>{if(active){setNotice(wealthRequestFailure(e));setReviewFailed(true);}}).finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[initial,initialPreview]);
  const act=async(work:()=>Promise<void>,refreshAllowed=false)=>{setBusy(true);setNotice('');try{await work();}catch(e){
    const code=(e as {response?:{data?:{detail?:{code?:string}}}})?.response?.data?.detail?.code;
    if(refreshAllowed&&plan.stage==='BUY'&&['QUOTE_CHANGED','AUTHORIZATION_EXPIRED','PREVIEW_REQUIRED'].includes(code??'')){
      try{const updated=await w.post<{plan:w.Plan;preview:w.PlanPreview}>(`/plans/${plan.plan_id}/refresh-review`,{version:plan.version});
        setPlan(updated.plan);setPreview(updated.preview);setConsent(false);setStage('PREVIEW');setCode('');setFactor({method:'totp'});
        setNotice('系统已自动核对并更新交易明细。请阅读当前明细并再次确认，原确认和验证码不再用于新版本。');
      }catch(reason){setNotice(wealthRequestFailure(reason));}
    }else setNotice(wealthRequestFailure(e));
  }finally{setBusy(false);}};
  const execute=async()=>{const current=await w.post<w.Plan>(`/plans/${plan.plan_id}/execute`,{version:plan.version});setPlan(current);setPreview(null);setCode('');onActivity();
    const message=current.items.every(i=>i.status==='SETTLED')?'模拟即时成交成功，资金与持仓已刷新。':current.last_error?wealthRequestFailure({response:{data:{detail:current.last_error}}}):'部分结果需要核对，请刷新状态，勿重新创建订单。';setNotice(message);if(current.items.every(i=>i.status==='SETTLED'))onCompleted?.(message);};
  return <AgentStepCard stage="trade" title="本人确认与验证"><section className="plan-execution" aria-label="整份方案可信操作">
    <h3>银行交易方案</h3><p>每笔绑定付款账户、具体产品及披露；整份方案使用一次本人确认与验证码。</p>
    {!preview&&busy&&<p role="status">正在自动核对交易金额、可用资金和产品条件…</p>}
    {!preview&&!busy&&reviewFailed&&plan.status==='AWAITING_CONFIRMATION'&&<button onClick={()=>act(async()=>{
      setPreview(await w.post<w.PlanPreview>(`/plans/${plan.plan_id}/preview`,{version:plan.version}));setReviewFailed(false);
    },true)}>重试核对交易</button>}
    <button className="secondary" disabled={busy} onClick={()=>act(async()=>{const current=await w.get<w.Plan>(`/plans/${plan.plan_id}/progress`);setPlan(current);onActivity();if(current.items.length&&current.items.every(i=>i.status==='SETTLED'))onCompleted?.('全部成交已核实。');})}>刷新成交结果</button>
    <CommandReview rows={plan.items}/>
    {plan.next_action&&<p>{plan.next_action}</p>}
    {preview&&<section aria-label="整份银行预览"><h3>逐笔核对（{preview.items.length}笔）</h3>{preview.items.map(item=><article key={item.authorization_id}>
      <strong>{item.product_name}</strong><p>{item.command.direction==='purchase'?`申购 ${w.money(item.command.amount_fen)}`:`赎回 ${item.command.units}份`}</p>
      <p>估算费用 {w.money(item.estimate?.fee_fen)} · {item.command.direction==='purchase'?`估算份额 ${item.estimate?.units??'未知'}`:`估算到账 ${w.money(item.estimate?.cash_fen)}`} · 净值日期 {item.estimated_nav_date??'未知'}</p>
      <p>计价 {item.pricing_date} · 确认 {item.confirm_date} · 到账 {item.cash_date} · 预览有效至 {beijingTime(item.expires_at)}</p>
      <p>{item.notice}</p><details><summary>完整披露</summary>{visibleDisclosures(item.disclosure).map(([key,value])=><p key={key}>{disclosureLabels[key]||'披露事项'}：{displayDisclosure(value)}</p>)}</details>
    </article>)}
      {stage==='PREVIEW'&&<><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/>本人已阅读全部交易与披露，理解本金亏损、费用及流动性，确认采用整份方案</label><button disabled={busy||!consent} onClick={()=>act(async()=>{await w.post(`/plans/${preview.plan_id}/confirm`,{version:preview.version,items:preview.items.map(i=>({authorization_id:i.authorization_id,digest:i.digest,disclosure_version:i.command.disclosure_version,accepted:true}))});setStage('CONFIRMED');},true)}>一次确认整份方案</button></>}
      {stage==='CONFIRMED'&&<><SecondFactor key={preview.verification_reference_id} purpose="wealth" referenceId={preview.verification_reference_id} factor={factor} onChange={setFactor} disabled={busy}/><label>6位验证码<input aria-label="整份方案验证码" inputMode="numeric" autoComplete="off" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))} disabled={busy}/></label><button disabled={busy||!factor||code.length!==6} onClick={()=>act(async()=>{if(!factor)return;await w.post(`/plans/${preview.plan_id}/verify`,{version:preview.version,proof:code,method:factor.method,challenge_id:factor.method==='sms'?factor.challengeId:undefined});setCode('');setStage('VERIFIED');await execute();},true)}>一次验证并提交整份方案</button></>}
      {stage==='VERIFIED'&&<button disabled={busy} onClick={()=>act(execute)}>恢复提交（沿用原号）</button>}
    </section>}
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
  </section></AgentStepCard>;
}
