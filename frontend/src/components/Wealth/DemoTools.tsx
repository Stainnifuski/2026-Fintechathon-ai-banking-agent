import {useEffect,useState} from 'react';
import * as w from '../../services/wealth';
import {beijingTime} from '../../utils/time';

interface Run {run_id:string; expected_version:number; archived_at?:string; fixture_version?:string; account_id?:string; counts?:Record<string,number>; deletable?:boolean; blocking_reasons?:string[]}
interface Current extends Run {enabled:boolean; writable:boolean; epoch:number; balance_fen:number; held_fen:number; available_fen:number; business_at:string}
interface Preview {runs: (Run & {counts:Record<string,number>})[];preview_digest:string;irreversible:boolean}
const recordLabel:Record<string,string>={operations:'订单',lots:'持仓批次',auths:'授权预览',plans:'方案',conversations:'会话',tasks:'任务',transactions:'流水'};
const describeCounts=(counts:Record<string,number>)=>Object.entries(counts).map(([key,value])=>`${recordLabel[key]||key} ${value}`).join('，');

export default function DemoTools({changed,userId,revision}:{changed:()=>Promise<void>;userId:string;revision:number}) {
  const pendingKey=`wealth-demo-request-${userId}`;
  const [current,setCurrent]=useState<Current|null>(null);
  const [archives,setArchives]=useState<Run[]>([]);
  const [selected,setSelected]=useState<string[]>([]);
  const [preview,setPreview]=useState<Preview|null>(null);
  const [amount,setAmount]=useState('');
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const [pending,setPending]=useState<{path:string;body:unknown}|null>(()=>{
    try{return JSON.parse(localStorage.getItem(pendingKey)||'null');}catch{return null;}
  });
  const load=async () => {
    const scope=await w.get<Current>('/demo/current');setCurrent(scope);
    if(scope.enabled)setArchives((await w.get<{items:Run[]}>('/demo/archives')).items);
  };
  useEffect(()=>{void load().catch(()=>setCurrent(null));},[revision]);
  const savePending=(request:{path:string;body:unknown}|null)=>{
    if(request)localStorage.setItem(pendingKey,JSON.stringify(request));else localStorage.removeItem(pendingKey);
    setPending(request);
  };
  const write=async (request:{path:string;body:unknown})=>{
    setBusy(true);setNotice('');savePending(request);
    try {await w.post(request.path,request.body);savePending(null);setPreview(null);setSelected([]);await load();await changed();setNotice('测试操作已完成。');}
    catch(e){
      const response=(e as {response?:{status:number;data?:{detail?:{message?:string}}}}).response;
      if(response && response.status<500)savePending(null);
      setNotice(response?.data?.detail?.message || '结果尚未核对，请使用原请求重试。');
    }finally{setBusy(false);}
  };
  if(!current?.enabled)return null;
  const disabled=busy||!!pending||!current.writable;
  return <details><summary>模拟测试工具</summary><p>仅当前本人可处置轮次；普通账户不受影响。重置保留旧轮次，删除不可恢复。</p>
    {current.writable ? <><p>当前模拟轮次 · 账面 {w.money(current.balance_fen)} · 占款 {w.money(current.held_fen)} · 可用 {w.money(current.available_fen)}</p>
      <p>模拟业务时间：{beijingTime(current.business_at)}，顶部时钟仍为现实北京时间。</p>
      <button disabled={disabled} onClick={()=>{if(window.confirm('归档当前测试轮次并创建基线20,000元的新轮次？旧轮次未提交授权失效，旧订单事实保留。'))void write({path:'/demo/reset',body:{run_id:current.run_id,expected_version:current.expected_version,request_id:crypto.randomUUID()}});}}>重置并归档当前轮次</button>
      <label>测试调整金额（元）<input inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/></label>
      {(['credit','debit'] as const).map(action=><button key={action} disabled={disabled||!amount} onClick={()=>{if(window.confirm(`确认${action==='credit'?'增加':'减少'}当前模拟资金 ${amount} 元？`))void write({path:'/demo/adjust',body:{run_id:current.run_id,expected_version:current.expected_version,request_id:crypto.randomUUID(),amount,action}});}}>{action==='credit'?'增加模拟资金':'减少模拟资金'}</button>)}</>:<p>请先选择可处置模拟轮次，普通账户不能使用测试写工具。</p>}
    <h4>旧归档轮次</h4><button disabled={busy||!!pending} onClick={()=>{setPreview(null);setSelected(archives.filter(run=>run.deletable!==false).map(run=>run.run_id));}}>选择全部可删除归档</button>{archives.map((run,index)=><label key={run.run_id} className="wealth-check"><input type="checkbox" disabled={busy||!!pending||run.deletable===false} checked={selected.includes(run.run_id)} onChange={e=>{setPreview(null);setSelected(e.target.checked?[...selected,run.run_id]:selected.filter(id=>id!==run.run_id));}}/>第 {index + 1} 个归档 · {run.archived_at?beijingTime(run.archived_at):'归档时间未知'} · {run.counts?describeCounts(run.counts):'计数待核对'} · {run.deletable===false?run.blocking_reasons?.join('；'):'可选择，提交时重验'}</label>)}
    <button disabled={busy||!!pending||!selected.length} onClick={async()=>{
      setBusy(true);setNotice('');try{setPreview(await w.post<Preview>('/demo/delete-preview',{runs:archives.filter(r=>selected.includes(r.run_id)).map(({run_id,expected_version})=>({run_id,expected_version}))}));}
      catch(e){setNotice((e as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail?.message || '删除范围预览失败。');}finally{setBusy(false);}
    }}>预览选中归档删除范围</button>
    {preview && <section><h4>不可恢复删除范围</h4>{preview.runs.map((run,index)=><p key={run.run_id}>第 {index + 1} 个归档 · {describeCounts(run.counts)}</p>)}
      <button disabled={busy||!!pending} onClick={()=>{if(window.confirm('本人确认物理删除上述选中旧归档及其独占测试记录？共享产品、身份评估及当前轮次保留。'))void write({path:'/demo/delete',body:{runs:preview.runs.map(({run_id,expected_version})=>({run_id,expected_version})),preview_digest:preview.preview_digest,confirmed:true,request_id:crypto.randomUUID()}});}}>本人确认物理删除</button>
    </section>}
    {pending && <button disabled={busy} onClick={()=>{void write(pending);}}>按原请求核对并重试</button>}
    {notice && <p role="status">{notice}</p>}
  </details>;
}
