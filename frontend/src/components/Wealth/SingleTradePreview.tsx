import Modal from '../Modal';
import {useTaskCompletion} from '../NovaTaskPresentation';
import {useState} from 'react';
import * as w from '../../services/wealth';
import type {SecondFactor as Factor} from '../../services/bank';
import {SecondFactor} from '../SecondFactor';
import {beijingTime} from '../../utils/time';
import {disclosureLabels,displayDisclosure,visibleDisclosures} from './disclosureView';
import {statusLabels} from './tradeStatus';

/** Bank preview/confirm/verify/execute shared by the three B4 input modes. */
export default function SingleTradePreview({preview:initial,onClose,onActivity,onCompleted,redemption}:{preview:w.Preview;onClose:()=>void;onActivity:()=>void;onCompleted?:(message:string)=>void;redemption?:{holding_ratio:string;estimated_market_value_fen:number;notice:string}}){
  const finishTask=useTaskCompletion();
  const [preview,setPreview]=useState(initial);const [consent,setConsent]=useState(false);
  const [factor,setFactor]=useState<Factor|null>({method:'totp'});const [code,setCode]=useState('');
  const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');const [done,setDone]=useState(false);
  const act=async(work:()=>Promise<void>)=>{setBusy(true);setNotice('');try{await work();}catch(error){setNotice((error as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??'请求失败，请稍后核对结果');}finally{setBusy(false);}};
  const execute=async()=>{const result=await w.post<w.Order>(`/previews/${preview.authorization_id}/execute`,{});setDone(true);setCode('');const message=`${statusLabels[result.status]??'状态待核对'}；${result.status==='SETTLED'?'模拟即时成交成功，资金与持仓已刷新':'结果尚未完成，请先刷新核对，勿重复提交'}。`;setNotice(message);onCompleted?.(message);onActivity();if(result.status==='SETTLED'&&finishTask){onClose();finishTask();}};
  const direction=preview.command.direction==='purchase'?'申购':'赎回';
  return <Modal className="wealth-modal" scope="wealth wealth-page"><section className="card" role="dialog" aria-modal="true" aria-label={`${direction}本人确认`}><h3>{preview.product_name} · {direction} {preview.command.direction==='purchase'?w.money(preview.command.amount_fen):`${preview.command.units}份`}</h3>
    {redemption&&<><p>拟赎回市值 {w.money(redemption.estimated_market_value_fen)} · 占本只总持仓 {(Number(redemption.holding_ratio)*100).toFixed(4)}%</p><p>{redemption.notice}</p></>}
    {preview.command.redemption_mode==='quick'&&<p>快速赎回：成功后立即到账且不可撤销，受理时银行重新核对净值、合同窗口及每日额度；失败不会自动改为普通赎回。</p>}
    {preview.command.redemption_mode==='quick'&&<section aria-label="快赎银行绑定报价"><p>本次绑定到账 {w.money(preview.command.quoted_cash_fen)} · 净值可用时间 {preview.estimate?.nav_available_at?beijingTime(preview.estimate?.nav_available_at):'未知，需重新预览'}</p><p>北京时间窗口 {preview.estimate?.contract?`${preview.estimate?.contract.start}—${preview.estimate?.contract.end}（结束不含端点）`:'待银行核验'}；{preview.estimate?.contract?.window==='trading_day'?'仅模拟交易日':'自然日，维护窗口除外'}。</p><p>本自然日已支付 {w.money(preview.estimate?.daily_used_fen)} · 受理前剩余额度 {w.money(preview.estimate?.daily_remaining_fen)}，同轮次所有账户共享。</p></section>}
    <p>所选付款账户 · 估算净值 {preview.estimated_nav??'未知'}（{preview.estimated_nav_date??'未知'}）· 费用 {w.money(preview.estimate?.fee_fen)} · {preview.command.direction==='purchase'?`估算份额 ${preview.estimate?.units??'未知'}`:`估算到账 ${w.money(preview.estimate?.cash_fen)}`}</p>
    <p>计价 {preview.pricing_date} · 确认 {preview.confirm_date} · 到账 {preview.cash_date} · 有效至 {beijingTime(preview.expires_at)}</p><p>{preview.notice}</p>
    <details><summary>产品披露</summary>{visibleDisclosures(preview.disclosure).map(([key,value])=><p key={key}>{disclosureLabels[key]??'披露事项'}：{displayDisclosure(value)}</p>)}</details>
    {!done&&preview.authorization_status==='AWAITING_CONFIRMATION'&&<><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/>本人已阅读披露并同意本笔{direction}</label><button disabled={busy||!consent} onClick={()=>act(async()=>{const result=await w.post<{status:string}>(`/previews/${preview.authorization_id}/confirm`,{digest:preview.digest,disclosure_version:preview.command.disclosure_version,accepted:true});setPreview({...preview,authorization_status:result.status});})}>本人确认本笔{direction}</button></>}
    {!done&&preview.authorization_status==='AWAITING_MFA'&&<><SecondFactor purpose="wealth" referenceId={preview.authorization_id} factor={factor} onChange={setFactor} disabled={busy}/><label>6位验证码<input aria-label={`${direction}验证码`} inputMode="numeric" autoComplete="off" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></label><button disabled={busy||!factor||code.length!==6} onClick={()=>act(async()=>{if(!factor)return;const result=await w.post<{status:string}>(`/previews/${preview.authorization_id}/verify`,{proof:code,method:factor.method,challenge_id:factor.method==='sms'?factor.challengeId:undefined});setPreview({...preview,authorization_status:result.status});setCode('');await execute();})}>验证并提交{direction}</button></>}
    {!done&&preview.authorization_status==='VALID'&&<button disabled={busy} onClick={()=>act(execute)}>恢复提交（沿用原号）</button>}
    {notice&&<p role="alert">{notice}</p>}<button className="secondary" disabled={busy} onClick={onClose}>关闭{direction}预览</button>
  </section></Modal>;
}
