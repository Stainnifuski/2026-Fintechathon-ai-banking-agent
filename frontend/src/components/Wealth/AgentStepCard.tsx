import {createContext,useContext,useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';

export interface StepContext {
 taskId:string;focusedStage?:string;instruction?:{id:string;action:string;stage:string|null};
 focus:(stage:string,label:string)=>void; draft?:(data:Record<string,unknown>)=>void;
 completed:string[];active?:string;register:(stage:string)=>()=>void;complete:(...stages:string[])=>void;
 backtrack:(stage:string)=>void;
 reopenedStage?:string;
}

export function useStepReady(stage:string,ready:boolean){
 const context=useContext(WealthStepContext);
 useEffect(()=>{if(ready)context?.complete(stage);},[ready,stage,context?.complete]);
}
export const WealthStepContext=createContext<StepContext|null>(null);

export function useStepInstruction(stage:string,handler:(action:string)=>void|Promise<void>){
 const context=useContext(WealthStepContext);const consumed=useRef<string>();const callback=useRef(handler);callback.current=handler;
 useEffect(()=>{const command=context?.instruction;
  if(!command||command.stage!==stage||consumed.current===command.id)return;
  consumed.current=command.id;void callback.current(command.action);
 },[context?.instruction?.id,context?.instruction?.stage,stage]);
}

export default function AgentStepCard({stage,title,children,onAdvance,nextFocus,canContinue=false}:{stage:string;title:string;children:ReactNode;onAdvance?:(action:string)=>void|Promise<void>;nextFocus?:[string,string];canContinue?:boolean}){
 const context=useContext(WealthStepContext);const [collapsed,setCollapsed]=useState(false);
 useEffect(()=>context?.register(stage),[stage,context?.register]);
 useStepInstruction(stage,action=>{if(context?.active!==stage)return;if(action==='next_batch'&&onAdvance)return onAdvance(action);if(canContinue)context?.complete(stage);if(context&&nextFocus)context.focus(...nextFocus);});
 if(!context)return <>{children}</>;
 const done=context.completed.includes(stage),visible=done||context.active===stage;
 const focused=context.focusedStage===stage;
 return <section hidden={!visible} data-wealth-active={!done&&visible?'true':undefined} className={`wealth-step-card ${done?'is-completed':''} ${focused?'is-focused':''}`} aria-label={title} onFocusCapture={()=>{if(!done)context.focus(stage,title);}} onPointerDown={()=>{if(!done)context.focus(stage,title);}}>
  <header><strong>{title}{done?' · 已完成':''}</strong><div className="wealth-step-actions">{done&&<button type="button" className="secondary" onClick={()=>context.backtrack(stage)}>修改此步骤</button>}<button type="button" className="secondary" aria-expanded={done?collapsed:!collapsed} onClick={()=>setCollapsed(v=>!v)}>{done?(collapsed?'收起摘要':'查看摘要'):(collapsed?'展开':'折叠')}</button></div></header>
  <div hidden={done?!collapsed:collapsed} className="wealth-step-body"><fieldset disabled={done||!visible}>{children}</fieldset></div>
  {!done&&canContinue&&<button type="button" onClick={()=>context.complete(stage)}>继续</button>}
 </section>;
}
