import {useState,useRef} from 'react';
import {useEffect} from 'react';
import SingleTradePreview from './SingleTradePreview';
import * as w from '../../services/wealth';
export default function TaskControlPanel({tasks,onChanged,disabled}:{tasks:w.WealthTask[];onChanged:()=>void;disabled:boolean}){
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
  const [preview,setPreview]=useState<w.Preview|null>(null);
  const [redemption,setRedemption]=useState<{holding_ratio:string;estimated_market_value_fen:number;notice:string}|undefined>();
  const generation=useRef(0);
  const taskKey=JSON.stringify(tasks.map(t=>[t.task_id,t.version,t.control_mode,t.status]));
  useEffect(()=>{++generation.current;setPreview(null);setRedemption(undefined);setBusy(false);return()=>{++generation.current;};},[taskKey]);
  const repreview=async(task:w.WealthTask)=>{const ticket=generation.current;setBusy(true);setMessage('');setPreview(null);try{const result=await w.post<{task:w.WealthTask;preview:w.Preview;redemption?:{holding_ratio:string;estimated_market_value_fen:number;notice:string}}>(`/tasks/${task.task_id}/repreview`,{request_id:crypto.randomUUID(),expected_version:task.version});if(ticket!==generation.current)return;setRedemption(result.redemption);setPreview(result.preview);setMessage('已重读银行并生成新预览，尚未确认或下单。');}catch(e){if(ticket!==generation.current)return;setMessage((e as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail?.message??'重新核对未完成，请刷新原任务。');}finally{if(ticket===generation.current)setBusy(false);}};
  const active=tasks.filter(t=>!['COMPLETED','CANCELLED','PARTIAL_COMPLETED','FAILED'].includes(t.status));
  const control=async(task:w.WealthTask,action:'pause'|'resume'|'handoff')=>{
    const ticket=generation.current;setBusy(true);setMessage('');
    try{await w.post(`/tasks/${task.task_id}/${action}`,{request_id:crypto.randomUUID(),expected_version:task.version});
      if(ticket!==generation.current)return;setMessage(action==='handoff'?'已申请人工接管，等待获授权操作员领取；不会自动提交交易。':action==='pause'?'任务已暂停，尚未提交的旧授权失效；已受理订单继续保留。':'任务已继续，交易仍需重新核对、预览和本人确认与验证。');onChanged();
    }catch(e){if(ticket!==generation.current)return;setMessage((e as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail?.message??'控制请求未完成，请按原任务刷新核对。');onChanged();}
    finally{if(ticket===generation.current)setBusy(false);}
  };
  return <details><summary>未完成任务与人工接管（{active.length}）</summary>
    <p>暂停和接管不会取消已受理订单；相关交易仍受银行权限检查。</p>
    {!active.length&&<p>当前范围暂无未完成任务。</p>}
    {active.map(t=><article className="wealth-product" key={t.task_id}><strong>任务 {t.task_id.slice(-8)} · v{t.version} · {t.account_id}</strong>
      <p>{t.control_mode==='HUMAN'?'操作员处理中':t.control_mode==='HANDOFF_PENDING'?'等待人工领取':t.status==='PAUSED'?'已暂停':'助手处理'} · {t.status}</p>
      {t.waiting_reason==='REVALIDATION_REQUIRED'&&<p>恢复后需要重新读取账户资料并核验，尚未提交的旧授权不可继续使用。</p>}
      {t.control_mode==='AGENT'&&<><button className="secondary" disabled={disabled||busy||t.status==='PAUSED'} onClick={()=>{void control(t,'pause');}}>暂停该任务</button><button className="secondary" disabled={disabled||busy||t.status!=='PAUSED'} onClick={()=>{void control(t,'resume');}}>继续该任务</button><button className="secondary" disabled={disabled||busy} onClick={()=>{void control(t,'handoff');}}>申请人工接管</button></>}
      {t.control_mode==='AGENT'&&t.status==='WAITING'&&!t.plan_id&&<button className="secondary" disabled={disabled||busy} onClick={()=>{void repreview(t);}}>重新核对未提交动作</button>}
      {t.plan_id&&<p>整份方案请回到配置核对，修订比例后重新准备差额；不能沿用接管前的预览。</p>}
      {t.notes.map((n,i)=><p key={i}>{n.action} · {n.reason||'无备注'}</p>)}
    </article>)}
    {message&&<p role="status">{message}</p>}
    {preview&&<SingleTradePreview preview={preview} redemption={redemption} onClose={()=>{setPreview(null);onChanged();}} onCompleted={message=>{setMessage(message);setPreview(null);setRedemption(undefined);}} onActivity={onChanged}/>}
  </details>;
}
