import {useEffect,useRef,useState} from 'react';
import * as w from '../../services/wealth';
import {beijingTime} from '../../utils/time';
import {checkOperatorReview,reviewCommands,operatorPortfolio,portfolioAdjustmentNote} from '../../services/wealthOperatorReview';
import type {OperatorContext} from '../../services/wealthOperatorReview';
import CommandReview from './CommandReview';
import AllocationReview from './AllocationReview';

export default function OperatorPanel({userId,revision,onActivity}:{userId:string;revision:number;onActivity:()=>void}) {
  const [rows,setRows]=useState<w.WealthTask[]|null>(null);
  const [review,setReview]=useState<{facts:w.Assets;context:OperatorContext;allocation:ReturnType<typeof operatorPortfolio>}|null>(null);
  const [note,setNote]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const epoch=useRef(0);
  const clear=()=>{setReview(null);setNote('');};
  const load=async()=>{
    const request=++epoch.current;clear();setBusy(true);setError('');
    try{const data=await w.get<{items:w.WealthTask[]}>('/operator/tasks');if(request===epoch.current)setRows(data.items);}
    catch{if(request===epoch.current){setRows(null);setError('操作员任务读取失败，请重新登录核对权限。');}}
    finally{if(request===epoch.current)setBusy(false);}
  };
  useEffect(()=>{void load();return()=>{++epoch.current;};},[userId,revision]);
  const act=async(task:w.WealthTask,action:'claim'|'note'|'release'|'facts')=>{
    const request=++epoch.current;setBusy(true);setError('');setNotice('');
    const submittedNote=note;
    if(action==='facts')clear();
    try{
      if(action==='facts'){
        const [facts,context]=await Promise.all([w.get<w.Assets>(`/operator/tasks/${task.task_id}/facts`),w.get<OperatorContext>(`/operator/tasks/${task.task_id}/context`)]);
        checkOperatorReview(task,userId,facts,context);
        const allocation=operatorPortfolio(context,facts);
        if(request===epoch.current)setReview({facts,context,allocation});
      }else{
        if(action!=='claim'&&(!review||review.context.task.task_id!==task.task_id||review.context.task.version!==task.version||!submittedNote.trim()))throw new Error('请先读取该任务当前资料并填写处理说明。');
        await w.post(`/operator/tasks/${task.task_id}/${action}`,{request_id:crypto.randomUUID(),expected_version:task.version,reason:action==='claim'?'':submittedNote});
        if(request!==epoch.current)return;
        clear();setNotice(action==='release'?'已交还，任务保持暂停。本人须重新核对并创建新预览，不能沿用旧授权。':action==='note'?'处理说明已保存；请重新读取资料后继续核对。':'已领取任务；请读取原账户资料。');
        await load();onActivity();
      }
    }catch(e){if(request===epoch.current){clear();setError((e as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail?.message||(e instanceof Error?e.message:'处理未成功，请刷新任务事实。'));}}
    finally{if(request===epoch.current)setBusy(false);}
  };
  if(!rows)return null;
  return <details><summary>操作员处理工作台（服务端角色）</summary>
    <p>领取、核对、备注和交还沿用原任务；本人确认和验证码仍由原用户完成。交还后保持暂停并重新核验。</p>
    <button disabled={busy} onClick={()=>{void load();}}>刷新待接管任务</button>
    {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
    {!rows.length&&<p>暂无待领取或本人正在处理的任务。</p>}
    {rows.map(task=>{const selected=review?.context.task.task_id===task.task_id&&review.context.task.version===task.version;const owned=task.control_mode==='HUMAN'&&task.owner_id===userId;
      return <article key={task.task_id} className="wealth-product"><h4>任务 {task.task_id.slice(-8)} · v{task.version}</h4>
        <p>{task.control_mode==='HANDOFF_PENDING'?'等待领取':owned?'本人已领取':'其他操作员处理中'} · 原账户 {task.account_id}</p>
        <ul>{task.nodes.map(node=><li key={node.node_id}>{node.kind}：{node.status}</li>)}</ul>
        {task.control_mode==='HANDOFF_PENDING'?<button disabled={busy} onClick={()=>{void act(task,'claim');}}>领取任务</button>:owned&&<button disabled={busy} onClick={()=>{void act(task,'facts');}}>读取原账户银行事实</button>}
        {selected&&review&&owned&&<section aria-label="领取任务核对资料">
          <h4>银行账户事实</h4>{review.facts.cash.map(account=><p key={account.account_id}>{account.account_id} · 账面 {w.money(account.balance_fen)} · 可用 {w.money(account.available_fen)} · 占款 {w.money(account.held_fen)}</p>)}
          {review.facts.orders.map(order=><p key={order.operation_id}>{order.operation_id} · {order.status}</p>)}
          <h4>原任务对话与方案</h4>
          {review.context.conversation?.history.map((turn,index)=><article key={index}><p>用户：{turn.user}</p><p>助手：{turn.reply.message}</p></article>)}
          {review.context.plan&&<p>方案 v{review.context.plan.version} · {review.context.plan.status}</p>}
          {review.allocation&&<AllocationReview key={`allocation:${task.task_id}:${task.version}`} {...review.allocation}
            onPropose={input=>setNote(portfolioAdjustmentNote(review.allocation!.plan,input))}/>}
          {review.context.plan&&!review.allocation&&<p>该任务没有保存完整整体快照，仅可核对原执行动作；不会临时拼造比例方案。</p>}
          <CommandReview key={`${task.task_id}:${task.version}`} rows={reviewCommands(review.context)} onAdjustment={setNote}/>
          <label>处理说明／待用户事项<textarea rows={4} aria-label={`${task.task_id}处理说明`} value={note} maxLength={1000} disabled={busy} onChange={e=>setNote(e.target.value)}/></label>
          <button disabled={busy||!note.trim()} onClick={()=>{void act(task,'note');}}>保存处理说明</button>
          <button disabled={busy||!note.trim()} onClick={()=>{void act(task,'release');}}>交还助手并重新核验</button>
        </section>}
        {task.notes.map((entry,i)=><p key={i}>{beijingTime(entry.occurred_at)} · {entry.action} · {entry.reason}</p>)}
      </article>;
    })}
  </details>;
}
