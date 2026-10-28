import {useEffect,useState,type FormEvent} from 'react';
import * as bank from '../services/bank';
import {localDateTimeInput,localTimeLabel,localZoneLabel,scheduledInputInstant} from '../utils/scheduledTaskTime';
import './scheduledTasks.css';

const labels:Record<string,string>={SCHEDULED:'等待时间',READY:'可以开始',STARTED:'已启动 Agent',RUNNING:'进行中',FINISHED:'已完成',
  WAITING_CONFIRMATION:'待确认转账',WAITING_VERIFICATION:'待验证转账',WAITING_EXECUTION:'等待自动转账',
  EXECUTING:'转账中',RECONCILING:'核对转账结果',
  SUCCEEDED:'转账成功',FAILED:'转账失败',CANCELLED:'已取消'};

const yuan=(fen:number)=>`¥${(fen/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
// 锁定资金在任务结束前不能被其他付款使用；结束后剩余部分退回活期。
function lockText(lock:bank.ScheduledLock,accounts:bank.Account[]){
  const card=accounts.find(a=>a.account_id===lock.account_id)?.card_number;
  const from=card?`（尾号 ${card.slice(-4)}）`:'';
  if(lock.status==='HELD')return `已锁定活期 ${yuan(lock.amount_fen)}${from}${lock.used_fen?` · 已用 ${yuan(lock.used_fen)} · 剩余 ${yuan(lock.remaining_fen)}`:''}`;
  return `锁定 ${yuan(lock.amount_fen)}${from} · 已用 ${yuan(lock.used_fen)}${lock.returned_fen?` · 已退回 ${yuan(lock.returned_fen)}`:''}`;
}

function errorMessage(error:unknown){
  const detail=(error as {response?:{data?:{detail?:{message?:string}|string}}})?.response?.data?.detail;
  return typeof detail==='string'?detail:detail?.message??'操作失败，请刷新后重试。';
}

export default function ScheduledTasks({accounts,onGoTransfer,onDispatch,onFundsChanged}:{accounts:bank.Account[];onGoTransfer:()=>void;onDispatch:(task:bank.ScheduledTask)=>void;onFundsChanged?:()=>void}){
  const lockable=accounts.filter(a=>['savings','checking'].includes(a.account_type));
  const [lockAmount,setLockAmount]=useState(''),[lockAccount,setLockAccount]=useState('');
  const lockAccountId=lockAccount||lockable[0]?.account_id||'';
  const [items,setItems]=useState<bank.ScheduledTask[]>([]),[description,setDescription]=useState('');
  const [scheduledAt,setScheduledAt]=useState(()=>localDateTimeInput(new Date(Date.now()+2*60_000))),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState('');
  const unfinished=items.filter(task=>!task.completed);
  const completed=items.filter(task=>task.completed);
  const refresh=()=>bank.scheduledTasks().then(setItems).catch(e=>setError(errorMessage(e))).finally(()=>setLoading(false));
  useEffect(()=>{void refresh();const timer=window.setInterval(()=>void refresh(),10000);return()=>window.clearInterval(timer);},[]);
  const create=async(event:FormEvent)=>{
    event.preventDefault();if(busy)return;setBusy(true);setError('');setNotice('');
    try{const instant=scheduledInputInstant(scheduledAt);
      if(new Date(instant).getTime()<=Date.now())throw new Error('执行时间必须晚于当前时间。');
      const lock=lockAmount.trim()?{amount:lockAmount.trim(),account_id:lockAccountId}:undefined;
      if(lock&&!(Number(lock.amount)>0))throw new Error('锁定金额必须大于0。');
      await bank.createScheduledTask(description.trim(),instant,lock);setDescription('');setLockAmount('');setScheduledAt(localDateTimeInput(new Date(Date.now()+2*60_000)));
      setNotice(lock?'自定义任务已保存，资金已从活期锁定；到时间后可点击“开始执行”。':'自定义任务已保存，到时间后可点击“开始执行”。');await refresh();if(lock)onFundsChanged?.();}
    catch(e){setError(e instanceof Error&&!('response' in e)?e.message:errorMessage(e));}finally{setBusy(false);}
  };
  const start=async(task:bank.ScheduledTask)=>{
    if(busy)return;setBusy(true);setError('');setNotice('');
    try{const started=await bank.startScheduledTask(task.task_id);await refresh();onDispatch(started);
      setNotice('已打开 NOVA 并开始发送任务描述；后续进度请在对话中查看。');}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  const end=async(task:bank.ScheduledTask,finished:boolean)=>{
    if(busy)return;setBusy(true);setError('');setNotice('');
    try{const ended=finished?await bank.finishScheduledTask(task.task_id):await bank.cancelScheduledTask(task.task_id);
      setNotice(`${finished?'任务已完成':'任务已取消'}${ended.lock?.returned_fen?`，${yuan(ended.lock.returned_fen)} 已退回活期`:''}。`);
      await refresh();onFundsChanged?.();}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  return <div className="scheduled-tasks-page">
    <div className="page-intro"><p className="eyebrow">SCHEDULED TASKS</p><h1>定时任务</h1>
      <p>查看定时转账和自定义任务。自定义任务到时间后由你启动，NOVA 才会根据描述办理。</p></div>
    <section className="card scheduled-tasks-create"><h2>新增自定义定时任务</h2>
      <form onSubmit={e=>void create(e)}><label>任务描述<textarea value={description} onChange={e=>setDescription(e.target.value)}
        maxLength={2000} required placeholder="例如：查询本月支出并帮我制定预算" /></label>
        <label>执行时间（设备本地时区：{localZoneLabel()}）<input type="datetime-local" required value={scheduledAt}
          min={localDateTimeInput(new Date(Date.now()+60_000))}
          onChange={e=>setScheduledAt(e.target.value)} /><small>当前本地时间：{localTimeLabel(new Date().toISOString())}</small></label>
        <fieldset className="scheduled-tasks-lock"><legend>锁定活期资金（可选）</legend>
          <label>锁定金额（元）<input type="number" min="0.01" step="0.01" value={lockAmount} placeholder="不锁定可留空"
            onChange={e=>setLockAmount(e.target.value)} /></label>
          <label>从账户<select value={lockAccountId} onChange={e=>setLockAccount(e.target.value)} disabled={!lockable.length}>
            {lockable.map(a=><option key={a.account_id} value={a.account_id}>{a.card_number}{typeof a.available_fen==='number'?` · 可用 ${yuan(a.available_fen)}`:''}</option>)}</select></label>
          <small>保存时立即锁定；可自由支配余额不足则保存失败。锁定的钱在任务结束前不能被其他付款使用，开始后只有本任务在 NOVA 对话中的付款可以使用，任务完成或取消后剩余部分退回活期。</small></fieldset>
        <button disabled={busy||!description.trim()||!scheduledAt}>保存任务</button></form>
      <p className="hint">定时转账请在“转账与收款”表单中创建并确认；确认时会锁定转账金额，可自由支配余额不足则设定失败。</p>
      <button type="button" className="secondary" onClick={onGoTransfer}>前往转账与收款</button></section>
    <section className="card scheduled-tasks-list"><div className="scheduled-tasks-list-head"><h2>定时任务列表</h2>
      <button type="button" className="secondary" onClick={()=>void refresh()} disabled={loading}>刷新</button></div>
      {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
      {loading?<p>正在读取定时任务…</p>:items.length===0?<p>还没有定时任务。</p>:<>
      {([['未完成',unfinished],['已完成',completed]] as const).map(([title,tasks])=><div className="scheduled-tasks-group" key={title}>
        <h3>{title}（{tasks.length}）</h3>
        {tasks.length===0?<p className="hint">暂无{title}的任务。</p>:<ul>{tasks.map(task=><li key={`${task.task_type}:${task.task_id}`}>
        <div><strong>{task.task_type==='transfer'?'定时转账':'自定义任务'}</strong><span>{task.task_type==='transfer'&&task.status==='READY'?'准备转账':labels[task.status]??task.status}</span></div>
        <p>{task.description}</p><small>发生时间：{localTimeLabel(task.scheduled_at)}（{localZoneLabel()}）</small>
        {task.lock&&<small className="scheduled-tasks-lock-state">{lockText(task.lock,accounts)}</small>}
        <div className="scheduled-tasks-actions">
          {task.task_type==='custom'&&task.can_start&&<button type="button" disabled={busy} onClick={()=>void start(task)}>开始执行</button>}
          {task.can_finish&&<button type="button" disabled={busy} onClick={()=>void end(task,true)}>完成任务</button>}
          {task.can_cancel&&<button type="button" className="secondary" disabled={busy} onClick={()=>void end(task,false)}>{task.lock?'取消任务并退回资金':'取消任务'}</button>}
        </div>
      </li>)}</ul>}</div>)}</>}
    </section>
  </div>;
}
