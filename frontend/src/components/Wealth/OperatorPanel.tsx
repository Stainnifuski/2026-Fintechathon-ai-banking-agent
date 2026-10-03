import {useEffect, useState} from 'react';
import * as w from '../../services/wealth';
import {beijingTime} from '../../utils/time';

export default function OperatorPanel() {
  const [rows,setRows]=useState<w.WealthTask[]|null>(null);
  const [note,setNote]=useState('');
  const [facts,setFacts]=useState<w.Assets|null>(null);
  const [context,setContext]=useState<{task:w.WealthTask;conversation:{history:{user:string;reply:{message:string}}[]}|null;plan:{version:number;status:string;data:{items:{command:w.Command}[]}}|null}|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const load=async () => setRows((await w.get<{items:w.WealthTask[]}>('/operator/tasks')).items);
  useEffect(() => {void load().catch(() => setRows(null));},[]);
  const act=async (task:w.WealthTask, action:'claim'|'note'|'release'|'facts') => {
    setBusy(true);setError('');
    try {
      if (action==='facts') {
        const [bankFacts, original]=await Promise.all([
          w.get<w.Assets>(`/operator/tasks/${task.task_id}/facts`),
          w.get<NonNullable<typeof context>>(`/operator/tasks/${task.task_id}/context`)]);
        setFacts(bankFacts);setContext(original);
      }
      else {
        await w.post(`/operator/tasks/${task.task_id}/${action}`,{request_id:crypto.randomUUID(),expected_version:task.version,reason:note});
        await load();
      }
    } catch (e) {setError((e as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail?.message || '处理未成功，请刷新任务事实。');}
    finally {setBusy(false);}
  };
  if (!rows) return null;
  return <details><summary>操作员处理工作台（服务端角色）</summary>
    <p>接管只处理任务与记录说明；本人确认和模拟授权仍由原用户完成。交还后须重新核验。</p>
    <button disabled={busy} onClick={() => {void load();}}>刷新待接管任务</button>
    <label>处理说明／待用户事项<textarea value={note} maxLength={1000} onChange={e => setNote(e.target.value)} /></label>
    {error && <p role="alert">{error}</p>}
    {rows.map(task => <article key={task.task_id} className="wealth-product"><h4>任务 {task.task_id.slice(-8)} · v{task.version}</h4>
      <p>{task.control_mode==='HANDOFF_PENDING'?'等待领取':`操作员 ${task.owner_id} 处理中`}</p>
      <ul>{task.nodes.map(node => <li key={node.node_id}>{node.kind}：{node.status}</li>)}</ul>
      {task.control_mode==='HANDOFF_PENDING' ? <button disabled={busy} onClick={() => {void act(task,'claim');}}>领取任务</button> : <>
        <button disabled={busy} onClick={() => {void act(task,'facts');}}>读取原账户银行事实</button>
        <button disabled={busy||!note.trim()} onClick={() => {void act(task,'note');}}>保存处理说明</button>
        <button disabled={busy||!note.trim()} onClick={() => {void act(task,'release');}}>交还助手并重新核验</button>
      </>}
      {task.notes.map((entry,i) => <p key={i}>{beijingTime(entry.occurred_at)} · {entry.action} · {entry.reason}</p>)}
    </article>)}
    {facts && <section><h4>银行账户事实</h4>{facts.cash.map(account => <p key={account.account_id}>{account.account_id} · 账面 {w.money(account.balance_fen)} · 可用 {w.money(account.available_fen)} · 占款 {w.money(account.held_fen)}</p>)}
      {facts.orders.map(order => <p key={order.operation_id}>{order.operation_id} · {order.status}</p>)}
    </section>}
    {context && <section><h4>原任务对话与方案</h4><p>任务 {context.task.task_id} · 原账户 {context.task.account_id}</p>
      {context.conversation?.history.map((turn,index)=><article key={index}><p>用户：{turn.user}</p><p>助手：{turn.reply.message}</p></article>)}
      {context.plan && <><p>方案 v{context.plan.version} · {context.plan.status}</p>{context.plan.data.items.map(item=><p key={item.command.operation_id}>{item.command.product_id} · {item.command.direction==='purchase'?w.money(item.command.amount_fen):`${item.command.units}份`} · 原操作号 {item.command.operation_id}</p>)}</>}
    </section>}
  </details>;
}
