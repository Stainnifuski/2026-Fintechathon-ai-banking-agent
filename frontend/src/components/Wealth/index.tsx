import type {NovaWealthView,NovaWealthCommand} from '../../services/nova';
import {wealthFeedback} from '../../utils/wealthFeedback';
import {useEffect,useState} from 'react';
import * as w from '../../services/wealth';
import type {Account} from '../../services/bank';
import StructuredActionPanel from './StructuredActionPanel';
import './styles.css';
import './wealthPage.css';
import {competitionEnvironment,type WealthContexts} from '../../utils/wealthEnvironment';
interface Props {accounts:Account[];revision:number;onActivity:()=>void;userId:string;developmentManual?:boolean;agentCommand?:NovaWealthCommand;agentContext?:string;agentTaskId?:string;agentSelection?:NovaWealthView['selected_products'];onView?:(view:NovaWealthView)=>void;onPrepared?:(id:string)=>Promise<void>}
type Contexts=WealthContexts;
const explain=(e:unknown)=>(e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message||(e instanceof Error?e.message:'读取未完成，请刷新后重试。');
/** Formal manual wealth entry. No research tool shell or separate chat workspace. */
export default function Wealth({revision,onActivity,userId,agentCommand,agentContext,agentTaskId,agentSelection,onView,onPrepared}:Props){
 const [assessment,setAssessment]=useState<w.Assessment|null>(null),[questionnaire,setQuestionnaire]=useState<w.Questionnaire|null>(null);
 const [contexts,setContexts]=useState<Contexts|null>(null),[answers,setAnswers]=useState<Record<string,number>>({});
 const [adult,setAdult]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[localRevision,setLocalRevision]=useState(0);
 useEffect(()=>{let live=true;setNotice('');void Promise.all([w.get<w.Assessment|null>('/assessments/current'),w.get<w.Questionnaire>('/questionnaire'),w.get<Contexts>('/contexts')]).then(async([a,q,c])=>{
  if(!live)return;
  const available=competitionEnvironment(c);
  if(available.selected!==c.selected){
    const selected=await w.post<{context_id:string;version:number}>('/contexts/select',{context_id:available.selected,version:c.version});
    available.version=selected.version;
    if(live)onActivity();
  }
  if(live){setAssessment(a);setQuestionnaire(q);setContexts(available);}
 }).catch(e=>{if(live)setNotice(explain(e));});return()=>{live=false;};},[userId,revision,localRevision]);
 const act=async(fn:()=>Promise<void>)=>{if(busy)return;setBusy(true);setNotice('');try{await fn();setLocalRevision(n=>n+1);}catch(e){setNotice(explain(e));}finally{setBusy(false);}};
 const assessmentReady=!!assessment?.confirmed&&Date.parse(assessment.expires_at)>Date.now();
 useEffect(()=>{const refresh=()=>{if(document.visibilityState==='visible')setLocalRevision(n=>n+1);};window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);return()=>{window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);};},[]);
 const changed=()=>{setLocalRevision(n=>n+1);onActivity();};
 return <section className="wealth-page">{!agentCommand&&<h1>理财</h1>}{!agentCommand&&<><p>真实基金资料与虚构银行理财分别标源，资金和交易均为比赛模拟。本金可能亏损；低风险和快赎能力不代表本金或收益保证。</p>
  <p className="consumer-note">比赛模拟即时成交：买入经本人确认和MFA后立即扣款并增加持仓；赎回立即到账。使用最近已公布有效净值，持有期、开放窗口及额度仍适用，成功后不可撤。资料不足时不会受理或生成等待订单。</p></>}
  {notice&&<p role="alert">{wealthFeedback(notice)}</p>}
  <details hidden={!!agentCommand&&agentCommand.kind!=='assessment'&&(assessmentReady||!['planning','recommend','purchase'].includes(agentCommand.kind))} open={agentCommand?true:undefined}><summary>本人风险评估</summary><p>{assessment?`模拟承受能力 R${assessment.capacity_level} · ${assessment.confirmed?'已确认':'待确认'}`:'尚未评估，可先查看客观产品资料'}</p>
   <details open={agentCommand&&!assessmentReady?true:undefined}><summary>填写／重新评估（本人回答，{questionnaire?.daily_limit===null?'测试中暂不限单日次数':`单日最多${questionnaire?.daily_limit??2}次`}）</summary>{questionnaire?.notice&&<p>{questionnaire.notice}</p>}{questionnaire?.questions.map(q=><label key={q.id}>{q.title}<select aria-label={q.title} value={answers[q.id]??''} disabled={busy} onChange={e=>setAnswers(v=>({...v,[q.id]:Number(e.target.value)}))}><option value="" disabled>请选择本人答案</option>{q.options.map((o,i)=><option key={o} value={i}>{o}</option>)}</select></label>)}
    <label><input type="checkbox" checked={adult} disabled={busy} onChange={e=>setAdult(e.target.checked)}/>我是成年、完全民事行为能力的模拟客户，以上由本人回答</label>
    <button disabled={busy||!adult||Object.keys(answers).length!==9} onClick={()=>void act(async()=>{setAssessment(await w.post<w.Assessment>('/assessments',{questionnaire_version:questionnaire?.version,profile_version:questionnaire?.profile_version,adult,full_capacity:adult,answers}));})}>提交本人答案</button>
   </details>{assessment&&!assessment.confirmed&&<button disabled={busy} onClick={()=>void act(async()=>{await w.post(`/assessments/${assessment.assessment_id}/confirm`,{});changed();})}>本人确认上述评估结果并继续原需求</button>}
  </details>
  {contexts&&(!agentCommand||!['planning','recommend','purchase'].includes(agentCommand.kind)||assessmentReady)&&<StructuredActionPanel agentCommand={agentCommand} agentContext={agentContext} agentTaskId={agentTaskId} agentSelection={agentSelection} onView={onView} onPrepared={onPrepared} key={`${userId}:${contexts.selected}`} userId={userId} revision={revision+localRevision} onActivity={changed}/>}
 </section>;
}
