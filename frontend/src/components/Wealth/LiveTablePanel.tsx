import {useEffect,useState} from 'react';
import * as w from '../../services/wealth';
import TableWorkbench from './TableWorkbench';
import {scopeKey} from './tableData';
export default function LiveTablePanel({panel,userId,revision,onActivity,active}:{panel:'orders'|'positions';userId:string;revision:number;onActivity:()=>void;active:boolean}){
  const [facts,setFacts]=useState<{owner:string;assets:w.Assets;tasks:w.WealthTask[]}|null>(null);const [error,setError]=useState('');const [reload,setReload]=useState(0);
  useEffect(()=>{if(!active)return;let live=true;setError('');
    void Promise.all([w.get<w.Assets>('/assets'),w.get<{items:w.WealthTask[]}>('/tasks')]).then(([assets,tasks])=>{if(live)setFacts({owner:userId,assets,tasks:tasks.items});}).catch((error:unknown)=>{if(live)setError((error as {response?:{status?:number}})?.response?.status===401?'登录已过期，请退出并重新登录；暂不可操作。':'模拟银行事实读取失败，请刷新；已有表格可能已过时，暂不可操作。');});
    return()=>{live=false;};
  },[active,userId,revision,reload]);
  return <section aria-label="登录账户双表"><p>这里读取登录账户的模拟银行记录，与F23样例无关。查询不需要重新评估；赎回仍需本人确认和验证码。</p>
    <button className="secondary" onClick={()=>setReload(n=>n+1)}>刷新登录账户记录</button>{error&&<p role="alert">{error}</p>}
    {facts?.owner===userId?<TableWorkbench assets={facts.assets} scopeKey={scopeKey(facts.assets,userId)} panel={panel} refreshKey={reload+revision} onActivity={()=>{setReload(n=>n+1);onActivity();}}
      blockedAccounts={error?facts.assets.cash.map(a=>a.account_id):facts.tasks.filter(t=>!['COMPLETED','PARTIAL_COMPLETED','CANCELLED','FAILED'].includes(t.status)&&(t.control_mode!=='AGENT'||t.status==='PAUSED')).map(t=>t.account_id)}/>:!error&&<p>正在读取本人订单与持仓…</p>}
  </section>;
}
