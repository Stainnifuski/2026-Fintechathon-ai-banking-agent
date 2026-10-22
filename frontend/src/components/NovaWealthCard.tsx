import {WealthStepContext} from './Wealth/AgentStepCard';
import {useEffect,useRef,useState,useCallback} from 'react';
import type {NovaWealthCard as Card} from '../services/nova';
import {updateNovaWealthCard,saveNovaWealthView} from '../services/nova';
import * as w from '../services/wealth';
import Wealth from './Wealth';
import PlanExecution from './Wealth/PlanExecution';
import {wealthRequestFailure} from '../utils/wealthFeedback';
import './Wealth/novaWealth.css';
import {stepOrder,activeStep,stepLabels} from './Wealth/stepFlow';

const titles:Record<Card['command']['kind'],string>={assets:'理财资产',positions:'我的持仓',returns:'理财收益',detail:'产品详情',compare:'产品比较',assessment:'本人风险评估',recommend:'目标产品推荐',planning:'理财规划',purchase:'申购',redeem:'赎回',resume:'恢复理财任务',abandon:'放弃草稿',clarify:'补充理财需求'};

/** Resumes bank references, never replays a chat message as a trade. */
function RecoveredPlan({id,onPrepared,onActivity}:{id:string;onPrepared:(id:string)=>Promise<void>;onActivity:()=>void}){
 const [plan,setPlan]=useState<w.Plan|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;void w.get<w.Plan>(`/plans/${id}/progress`).then(p=>{if(live)setPlan(p);}).catch(e=>{if(live)setError(wealthRequestFailure(e));});return()=>{live=false;};},[id]);
 const continued=useRef(false);
 const continuePurchase=async()=>{
  if(!plan||busy||continued.current)return;continued.current=true;setBusy(true);setError('');
  try{
   const result=await w.post<{plan:w.PortfolioPlan;prepared:w.PreparedPortfolio}>(`/plans/${plan.plan_id}/continue-purchase`,{version:plan.version});
   if(result.prepared.execution_plan&&result.prepared.execution_plan_id){await onPrepared(result.prepared.execution_plan_id);setPlan(result.prepared.execution_plan);}
   else setError('实际到账已核实，当前不需要继续申购。');
  }catch(e){setError(`${wealthRequestFailure(e)} 已到账资金保留为现金。`);continued.current=false;}
  finally{setBusy(false);onActivity();}
 };
 const needsPurchase=plan?.stage==='SELL'&&!!(plan as w.Plan&{portfolio_plan?:w.PortfolioPlan}).portfolio_plan?.planning;
 return <section><p>已恢复原银行方案；以原操作号核对结果，不重新下单。</p>{error&&<p role="alert">{error}</p>}
  {plan&&<PlanExecution key={`${plan.plan_id}:${plan.version}`} plan={plan} onActivity={onActivity} onCompleted={()=>{if(needsPurchase)void continuePurchase();}}/>}
  {needsPurchase&&plan?.items.length&&plan.items.every(i=>i.status==='SETTLED')?<button disabled={busy} onClick={()=>void continuePurchase()}>{busy?'正在核实实际到账…':'核实到账后继续申购（独立确认及第二次MFA）'}</button>:null}
 </section>;
}

export default function NovaWealthCard({card,userId,disabled,onUpdated,onActivity,focusedStage,onFocus}:{focusedStage?:string;onFocus:(stage:string,label:string)=>void;card:Card;userId:string;disabled:boolean;onUpdated:(cards:Card[])=>void;onActivity:()=>void}){
 const [expanded,setExpanded]=useState(true),[revision,setRevision]=useState(0),[error,setError]=useState('');
 // A newly prepared plan stays in the live shared component. Only a remount restores it.
 const [recovered]=useState(card.execution_plan_id);
 const restoredInstruction=useRef(card.instruction?.id);
 const current=useRef(card);current.current=card;
 const viewDraft=useRef(card.view_context??{visible_products:[],selected_products:[]});
 const saveDraft=useCallback((patch:Record<string,unknown>)=>{viewDraft.current={...viewDraft.current,...patch};void saveNovaWealthView(current.current,viewDraft.current).catch(()=>{});},[]);
 const [registered,setRegistered]=useState<string[]>([]);
 const registrations=useRef(new Map<string,number>());
 const [completed,setCompleted]=useState<string[]>(card.view_context?.completed_stages??[]);
 const completedRef=useRef(completed);
 const register=useCallback((stage:string)=>{
  registrations.current.set(stage,(registrations.current.get(stage)??0)+1);
  setRegistered(s=>s.includes(stage)?s:[...s,stage]);
  return()=>{const remaining=(registrations.current.get(stage)??1)-1;registrations.current.set(stage,remaining);if(!remaining)setRegistered(s=>s.filter(v=>v!==stage));};
 },[]);
 const complete=useCallback((...stages:string[])=>{
  const next=Array.from(new Set([...completedRef.current,...stages]));
  if(next.length===completedRef.current.length)return;
  completedRef.current=next;setCompleted(next);saveDraft({completed_stages:next});
 },[saveDraft]);
 const active=activeStep(stepOrder(card.command.kind),registered,completed);
 const backtrack=(stage:string)=>{void updateNovaWealthCard(current.current,{backtrack_stage:stage}).then(onUpdated).catch(e=>setError(wealthRequestFailure(e)));};
 const decideBacktrack=(accepted:boolean)=>{if(!card.pending_backtrack)return;void updateNovaWealthCard(current.current,{backtrack_token:card.pending_backtrack.token,accept_backtrack:accepted}).then(onUpdated).catch(e=>setError(wealthRequestFailure(e)));};
 const persist=async(id:string)=>{
  const cards=await updateNovaWealthCard(current.current,{execution_plan_id:id});
  const next=cards.find(c=>c.task_id===card.task_id);if(next)current.current=next;
  onUpdated(cards);
 };
 const activity=()=>{setRevision(n=>n+1);onActivity();};
 const restoredCashField=card.view_context?.cash_field;
 const command={...card.command,cash_field:restoredCashField==='investment'?'invest' as const:restoredCashField??card.command.cash_field,
  cash_yuan:card.view_context?.cash_yuan??card.command.cash_yuan,
  amounts_yuan:card.command.products.map((p,i)=>card.view_context?.amounts_yuan?.[p.product_id]??card.command.amounts_yuan[i]??null)};
 return <WealthStepContext.Provider value={{taskId:card.task_id,focusedStage,instruction:card.instruction?.id===restoredInstruction.current?undefined:card.instruction,focus:onFocus,draft:saveDraft,completed,active,register,complete,backtrack,reopenedStage:card.reopen_stage}}><section className="nova-wealth-card wealth-page" aria-label={titles[card.command.kind]}>
  <div className="nova-wealth-task-head"><div><h3>{titles[card.command.kind]}</h3><small>{card.paused?'已暂停':card.closed?'草稿已放弃':'比赛模拟 · 待本人核对'}</small></div><button className="secondary" onClick={()=>setExpanded(v=>!v)}>{expanded?'折叠':'展开'}</button>{!card.closed&&!card.execution_plan_id&&<button className="nova-card-remove" aria-label="移除理财草稿" disabled={disabled} onClick={()=>{if(window.confirm('移除这个未提交的理财草稿？不会删除已有成交。'))void updateNovaWealthCard(current.current,{closed:true}).then(onUpdated).catch(e=>setError(wealthRequestFailure(e)));}}>×</button>}</div>
  {!card.closed&&card.execution_plan_id&&<button className="secondary" disabled={disabled} onClick={()=>void updateNovaWealthCard(current.current,{reset_draft:true}).then(onUpdated).catch(e=>setError(wealthRequestFailure(e)))}>修改未提交方案（旧确认失效）</button>}
  <details className="nova-wealth-more"><summary>更多操作</summary>{!card.closed&&<button className="secondary" disabled={disabled} onClick={()=>void updateNovaWealthCard(current.current,{paused:!card.paused}).then(onUpdated).catch(e=>setError(wealthRequestFailure(e)))}>{card.paused?'恢复并重新核对':'暂停后续步骤'}</button>}</details>
  {card.pending_backtrack&&<section role="dialog" aria-label="确认回退步骤" className="wealth-step-card"><h4>回到“{card.pending_backtrack.label}”？</h4><p>将重新处理：{card.pending_backtrack.invalidated.map(s=>stepLabels[s]??s).join(' → ')}。</p><p>受影响的后续输入、建议和旧交易确认将重置，需要重新填写和核对。其他无依赖的选择保留；已成交或已到账阶段不能回退。</p><button disabled={disabled} onClick={()=>decideBacktrack(false)}>取消，保留当前步骤</button><button disabled={disabled} onClick={()=>decideBacktrack(true)}>确认回退并重置</button></section>}
  {error&&<p role="alert">{error}</p>}{card.closed?<p>草稿已放弃；已有成交记录保持。</p>:card.paused?<p>后续步骤已暂停。已成交和已到账资金保留；恢复后重新核对，不复用旧确认或验证码。</p>:<div hidden={disabled||!expanded}>
   <fieldset disabled={!!card.pending_backtrack}>{card.command.kind==='clarify'?<p>{card.command.question}</p>:recovered?<RecoveredPlan id={recovered} onPrepared={persist} onActivity={activity}/>:<Wealth accounts={[]} revision={revision} onActivity={activity} userId={userId} agentCommand={command} agentContext={card.context_id} agentTaskId={card.task_id} agentSelection={card.view_context?.selected_products} onView={saveDraft} onPrepared={persist}/>}</fieldset>
  </div>}
 </section></WealthStepContext.Provider>;
}
