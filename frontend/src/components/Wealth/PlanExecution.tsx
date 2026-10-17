import CommandReview from './CommandReview';
import { useState } from 'react';
import * as w from '../../services/wealth';
import type { SecondFactor as Factor } from '../../services/bank';
import { SecondFactor } from '../SecondFactor';
import { beijingTime } from '../../utils/time';
import { disclosureLabels, displayDisclosure, visibleDisclosures } from './disclosureView';

interface Props { plan:w.Plan; onActivity:()=>void }
/** Existing plan endpoints and shared second factor; no alternative authority. */
export default function PlanExecution({plan:initial,onActivity}:Props){
  const [plan,setPlan]=useState(initial);const [preview,setPreview]=useState<w.PlanPreview|null>(null);
  const [consent,setConsent]=useState(false);const [stage,setStage]=useState<'PREVIEW'|'CONFIRMED'|'VERIFIED'>('PREVIEW');
  const [factor,setFactor]=useState<Factor|null>({method:'totp'});const [code,setCode]=useState('');
  const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');
  const act=async(work:()=>Promise<void>)=>{setBusy(true);setNotice('');try{await work();}catch(e){setNotice((e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message||'请求失败，请保留原操作号核对');}finally{setBusy(false);}};
  const execute=async()=>{const current=await w.post<w.Plan>(`/plans/${plan.plan_id}/execute`,{version:plan.version});setPlan(current);setPreview(null);setCode('');onActivity();
    setNotice(current.items.every(i=>i.status==='SETTLED')?'模拟即时成交成功，资金与持仓已刷新。':'部分结果需要核对，请按原操作号刷新，勿重新创建订单。');};
  return <section className="plan-execution" aria-label="整份方案可信操作">
    <h3>银行执行版本 v{plan.version}</h3><p>每笔绑定真实付款账户、具体产品、披露和原操作号；整份方案使用一次本人确认与验证码。</p>
    <button disabled={busy||!plan.items.length||plan.status!=='AWAITING_CONFIRMATION'} onClick={()=>act(async()=>{setPreview(await w.post<w.PlanPreview>(`/plans/${plan.plan_id}/preview`,{version:plan.version}));setConsent(false);setStage('PREVIEW');setCode('');setFactor({method:'totp'});})}>预览整份{plan.stage==='SELL'?'赎回':'申购'}</button>
    <button className="secondary" disabled={busy} onClick={()=>act(async()=>{setPlan(await w.get<w.Plan>(`/plans/${plan.plan_id}/progress`));onActivity();})}>刷新成交结果</button>
    <CommandReview rows={plan.items}/>
    {plan.next_action&&<p>{plan.next_action}</p>}
    {preview&&<section aria-label="整份银行预览"><h3>逐笔核对（{preview.items.length}笔）</h3>{preview.items.map(item=><article key={item.authorization_id}>
      <strong>{item.product_name} · {item.command.product_id}</strong><p>账户 {item.command.account_id} · {item.command.direction==='purchase'?`申购 ${w.money(item.command.amount_fen)}`:`赎回 ${item.command.units}份`} · 产品 v{item.command.product_version} · 披露 v{item.command.disclosure_version}</p>
      <p>估算费用 {w.money(item.estimate?.fee_fen)} · {item.command.direction==='purchase'?`估算份额 ${item.estimate?.units??'未知'}`:`估算到账 ${w.money(item.estimate?.cash_fen)}`} · 净值日期 {item.estimated_nav_date??'未知'}</p>
      <p>计价 {item.pricing_date} · 确认 {item.confirm_date} · 到账 {item.cash_date} · 预览有效至 {beijingTime(item.expires_at)}</p>
      <p>{item.notice}</p><details><summary>完整披露 v{item.command.disclosure_version}</summary>{visibleDisclosures(item.disclosure).map(([key,value])=><p key={key}>{disclosureLabels[key]||key}：{displayDisclosure(value)}</p>)}</details>
    </article>)}
      {stage==='PREVIEW'&&<><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/>本人已阅读全部交易与披露，理解本金亏损、费用及流动性，确认采用整份方案</label><button disabled={busy||!consent} onClick={()=>act(async()=>{await w.post(`/plans/${preview.plan_id}/confirm`,{version:preview.version,items:preview.items.map(i=>({authorization_id:i.authorization_id,digest:i.digest,disclosure_version:i.command.disclosure_version,accepted:true}))});setStage('CONFIRMED');})}>一次确认整份方案</button></>}
      {stage==='CONFIRMED'&&<><SecondFactor key={preview.verification_reference_id} purpose="wealth" referenceId={preview.verification_reference_id} factor={factor} onChange={setFactor} disabled={busy}/><label>6位验证码<input aria-label="整份方案验证码" inputMode="numeric" autoComplete="off" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))} disabled={busy}/></label><button disabled={busy||!factor||code.length!==6} onClick={()=>act(async()=>{if(!factor)return;await w.post(`/plans/${preview.plan_id}/verify`,{version:preview.version,proof:code,method:factor.method,challenge_id:factor.method==='sms'?factor.challengeId:undefined});setCode('');setStage('VERIFIED');await execute();})}>一次验证并提交整份方案</button></>}
      {stage==='VERIFIED'&&<button disabled={busy} onClick={()=>act(execute)}>恢复提交（沿用原号）</button>}
    </section>}
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
  </section>;
}
