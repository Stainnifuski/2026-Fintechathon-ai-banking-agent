import {useEffect,useState} from 'react';
import * as w from '../../services/wealth';
import type {Account} from '../../services/bank';
import StructuredActionPanel from './StructuredActionPanel';
import './styles.css';
interface Props {accounts:Account[];revision:number;onActivity:()=>void;userId:string;developmentManual?:boolean}
interface Contexts {selected:string;version:number;items:{context_id:string;label:string;mode:string}[]}
const explain=(e:unknown)=>(e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message||'读取未完成，请刷新后重试。';
/** Formal manual wealth entry. No research tool shell or separate chat workspace. */
export default function Wealth({revision,onActivity,userId}:Props){
 const [assessment,setAssessment]=useState<w.Assessment|null>(null),[questionnaire,setQuestionnaire]=useState<w.Questionnaire|null>(null);
 const [contexts,setContexts]=useState<Contexts|null>(null),[answers,setAnswers]=useState<Record<string,number>>({});
 const [adult,setAdult]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[localRevision,setLocalRevision]=useState(0);
 useEffect(()=>{let live=true;setNotice('');void Promise.all([w.get<w.Assessment|null>('/assessments/current'),w.get<w.Questionnaire>('/questionnaire'),w.get<Contexts>('/contexts')]).then(([a,q,c])=>{if(live){setAssessment(a);setQuestionnaire(q);setContexts(c);}}).catch(e=>{if(live)setNotice(explain(e));});return()=>{live=false;};},[userId,revision,localRevision]);
 const act=async(fn:()=>Promise<void>)=>{if(busy)return;setBusy(true);setNotice('');try{await fn();setLocalRevision(n=>n+1);}catch(e){setNotice(explain(e));}finally{setBusy(false);}};
 const changed=()=>{setLocalRevision(n=>n+1);onActivity();};
 return <section className="wealth-page"><h1>理财</h1><p>真实基金资料与虚构银行理财分别标源，资金和交易均为比赛模拟。本金可能亏损；低风险和快赎能力不代表本金或收益保证。</p>
  <label>资金上下文<select aria-label="资金上下文" disabled={busy} value={contexts?.selected??'legacy-v1'} onChange={e=>void act(async()=>{if(!contexts)return;await w.post('/contexts/select',{context_id:e.target.value,version:contexts.version});changed();})}>{contexts?.items.map(c=><option key={c.context_id} value={c.context_id}>{c.label}</option>)}</select></label>
  <p className="hint">仅选择本人已有授权范围，不复制余额。需要初始化演示账户时，请按工程准备说明使用受控初始化命令；无需产品研究或对话入口。</p>
  <button className="secondary" disabled={busy} onClick={()=>{changed();}}>刷新银行事实</button>
  {notice&&<p role="alert">{notice}</p>}
  <details><summary>本人风险评估</summary><p>{assessment?`模拟承受能力 R${assessment.capacity_level} · ${assessment.confirmed?'已确认':'待确认'}`:'尚未评估，可先查看客观产品资料'}</p>
   <details><summary>填写／重新评估（本人回答，单日最多2次）</summary>{questionnaire?.questions.map(q=><label key={q.id}>{q.title}<select aria-label={q.title} value={answers[q.id]??''} disabled={busy} onChange={e=>setAnswers(v=>({...v,[q.id]:Number(e.target.value)}))}><option value="" disabled>请选择本人答案</option>{q.options.map((o,i)=><option key={o} value={i}>{o}</option>)}</select></label>)}
    <label><input type="checkbox" checked={adult} disabled={busy} onChange={e=>setAdult(e.target.checked)}/>我是成年、完全民事行为能力的模拟客户，以上由本人回答</label>
    <button disabled={busy||!adult||Object.keys(answers).length!==9} onClick={()=>void act(async()=>{setAssessment(await w.post<w.Assessment>('/assessments',{questionnaire_version:questionnaire?.version,profile_version:questionnaire?.profile_version,adult,full_capacity:adult,answers}));})}>提交本人答案</button>
   </details>{assessment&&!assessment.confirmed&&<button disabled={busy} onClick={()=>void act(async()=>{await w.post(`/assessments/${assessment.assessment_id}/confirm`,{});changed();})}>本人确认上述评估结果并继续原需求</button>}
  </details>
  <StructuredActionPanel key={`${userId}:${contexts?.selected??'legacy-v1'}`} userId={userId} revision={revision+localRevision} onActivity={changed}/>
 </section>;
}
