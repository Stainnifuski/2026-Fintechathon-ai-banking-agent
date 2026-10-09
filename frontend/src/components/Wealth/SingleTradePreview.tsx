import {useState} from 'react';
import * as w from '../../services/wealth';
import type {SecondFactor as Factor} from '../../services/bank';
import {SecondFactor} from '../SecondFactor';
import {beijingTime} from '../../utils/time';
import {disclosureLabels,displayDisclosure} from './disclosureView';

/** Bank preview/confirm/verify/execute, also usable by B4's future value input. */
export default function SingleTradePreview({preview:initial,onClose,onActivity}:{preview:w.Preview;onClose:()=>void;onActivity:()=>void}){
  const [preview,setPreview]=useState(initial);const [consent,setConsent]=useState(false);
  const [factor,setFactor]=useState<Factor|null>({method:'totp'});const [code,setCode]=useState('');
  const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');const [done,setDone]=useState(false);
  const act=async(work:()=>Promise<void>)=>{setBusy(true);setNotice('');try{await work();}catch(error){setNotice((error as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??'请求失败，请保留原操作号核对');}finally{setBusy(false);}};
  const execute=async()=>{const result=await w.post<w.Order>(`/previews/${preview.authorization_id}/execute`,{});setDone(true);setCode('');setNotice(`原操作号 ${result.operation_id} · ${result.status}；已刷新资金和订单，受理不等于结算。`);onActivity();};
  return <div className="wealth-modal"><section className="card" role="dialog" aria-modal="true" aria-label="持仓赎回本人确认"><h3>{preview.product_name} · 赎回 {preview.command.units}份</h3>
    <p>账户 {preview.command.account_id} · 产品 v{preview.command.product_version} · 原操作号 {preview.command.operation_id}</p><p>估算净值 {preview.estimated_nav??'未知'}（{preview.estimated_nav_date??'未知'}）· 费用 {w.money(preview.estimate?.fee_fen)} · 估算到账 {w.money(preview.estimate?.cash_fen)}</p>
    <p>计价 {preview.pricing_date} · 确认 {preview.confirm_date} · 到账 {preview.cash_date} · 有效至 {beijingTime(preview.expires_at)}</p><p>预览不占用份额，实际到账以成交净值和扣费结果为准。</p>
    <details><summary>产品披露 v{preview.command.disclosure_version}</summary>{Object.entries(preview.disclosure).map(([key,value])=><p key={key}>{disclosureLabels[key]??key}：{displayDisclosure(value)}</p>)}</details>
    {!done&&preview.authorization_status==='AWAITING_CONFIRMATION'&&<><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/>本人已阅读披露并同意本笔赎回</label><button disabled={busy||!consent} onClick={()=>act(async()=>{const result=await w.post<{status:string}>(`/previews/${preview.authorization_id}/confirm`,{digest:preview.digest,disclosure_version:preview.command.disclosure_version,accepted:true});setPreview({...preview,authorization_status:result.status});})}>本人确认本笔赎回</button></>}
    {!done&&preview.authorization_status==='AWAITING_MFA'&&<><SecondFactor purpose="wealth" referenceId={preview.authorization_id} factor={factor} onChange={setFactor} disabled={busy}/><label>6位验证码<input aria-label="赎回验证码" inputMode="numeric" autoComplete="off" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></label><button disabled={busy||!factor||code.length!==6} onClick={()=>act(async()=>{if(!factor)return;const result=await w.post<{status:string}>(`/previews/${preview.authorization_id}/verify`,{proof:code,method:factor.method,challenge_id:factor.method==='sms'?factor.challengeId:undefined});setPreview({...preview,authorization_status:result.status});setCode('');await execute();})}>验证并提交赎回</button></>}
    {!done&&preview.authorization_status==='VALID'&&<button disabled={busy} onClick={()=>act(execute)}>恢复提交（沿用原号）</button>}
    {notice&&<p role="alert">{notice}</p>}<button className="secondary" disabled={busy} onClick={onClose}>关闭赎回预览</button>
  </section></div>;
}
