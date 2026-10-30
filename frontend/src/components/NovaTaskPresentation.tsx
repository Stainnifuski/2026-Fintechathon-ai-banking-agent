import Modal from './Modal';
import {createContext,useContext,useState,useRef,useLayoutEffect,useEffect,useCallback,useMemo,cloneElement,isValidElement,Suspense,type ReactNode,type ReactElement,type Dispatch,type SetStateAction} from 'react';
import {createPortal} from 'react-dom';
import {apiClient} from '../services/api';
import {flushNovaWealthView} from '../services/nova';
import {selectHandoffCandidate} from './novaHandoffCandidate';
import {offerManualPrefill,clearManualPrefill} from '../services/manualPrefill';
import {agentDrafts,manualDrafts} from '../services/wealthManualDraft';
import './novaTaskPresentation.css';

export type TaskMeta={key:string;session:string;label:string;destination:string;ready?:boolean;blocked?:boolean;service?:'flower'|'cake';finished?:boolean;summary?:string;released?:{mode:string;message:string;draft?:Record<string,unknown>;handoff?:Record<string,unknown>}|null};
type RecordItem={meta:TaskMeta;node:ReactNode;host:HTMLDivElement;anchor:HTMLElement;collapsed?:boolean;completed?:boolean;handedOff?:boolean;handoff?:Record<string,unknown>;ended?:boolean;uncertain?:boolean;uncertainMode?:'manual'|'cancel';receipt?:string;draft:Record<string,unknown>};
const TaskCompletionContext=createContext<(()=>void)|null>(null);
export const useTaskCompletion=()=>useContext(TaskCompletionContext);
const DraftContext=createContext<Record<string,unknown>|null>(null);
export function useTaskField<T>(key:string,initial:T|(()=>T)):[T,Dispatch<SetStateAction<T>>]{
 const draft=useContext(DraftContext),restored=useRef(draft?.[key] as T|undefined);
 const [value,setValue]=useState<T>(()=>restored.current!==undefined?restored.current:typeof initial==='function'?(initial as ()=>T)():initial);
 if(draft)draft[key]=value;
 useEffect(()=>{if(restored.current!==undefined)queueMicrotask(()=>setValue(restored.current!));},[]);
 return [value,setValue];
}
export function useTaskPrefill(values:Record<string,unknown>,handlers:Record<string,(value:unknown)=>void>){
 const previous=useRef(values),callbacks=useRef(handlers);callbacks.current=handlers;
 useEffect(()=>{for(const [key,value] of Object.entries(values)){if(value!==undefined&&JSON.stringify(value)!==JSON.stringify(previous.current[key]))callbacks.current[key]?.(value);}previous.current=values;},[JSON.stringify(values)]);
}
type Dialog={title:string;body:string;accept?:string;resolve:(value:boolean)=>void};
type Context={register:(item:RecordItem)=>()=>void;setWorkbench:(target:HTMLDivElement|null)=>void;confirm:(title:string,body:string,accept?:string)=>Promise<boolean>;handoff:(session:string)=>Promise<void>;flush:(session:string)=>Promise<void>;resume:(key:string)=>void;canHandoff:(session:string)=>boolean};
const Context=createContext<Context|null>(null);
export const useNovaPresentation=()=>{const value=useContext(Context);if(!value)throw new Error('Missing NOVA presentation provider');return value;};

function LiveTask({item,onToggle,onComplete,onCancel,onFocus,onRecover,onResume}:{item:RecordItem;onToggle:()=>void;onComplete:()=>void;onCancel:()=>void;onFocus:()=>void;onRecover:()=>void;onResume:()=>void}){
 const collapsed=!!item.collapsed,finished=!!item.meta.finished||!!item.completed;
 let node=item.node;
 if(isValidElement(node)){
  const props=(node as ReactElement<Record<string,unknown>>).props;
  node=cloneElement(node as ReactElement<Record<string,unknown>>,{disabled:!!props.disabled||!!item.uncertain||item.meta.ready===false||!!item.meta.blocked,onUpdated:(updated:unknown)=>{(props.onUpdated as ((value:unknown)=>void)|undefined)?.(updated);window.dispatchEvent(new CustomEvent('nova-task-updated',{detail:{key:item.meta.key}}));}});
 }
 // 人工接管后任务卡只留摘要；内容已带入对应页面的原表单，由本人在页面继续。
 const handedOff=item.handedOff||item.meta.released?.mode==='manual';
 if(item.ended||item.meta.released)return <section className="nova-task-summary" role="status"><strong>{item.meta.label}{handedOff?' · 已人工接管':''}</strong><p>{item.receipt??item.meta.released?.message??'任务已结束；已有银行记录保留。'}</p>{handedOff&&!item.meta.finished&&<button type="button" className="secondary" onClick={onResume}>前往表单继续办理</button>}</section>;
 return <section className="nova-task-surface" data-nova-task={item.meta.key} onFocusCapture={onFocus} onPointerDown={onFocus}>
  <header className="nova-task-header"><div><h3>{item.meta.label}</h3><small>{item.completed?'已完成':item.meta.summary??(finished?'已完成':'待本人办理')}</small></div><div className="nova-task-controls"><button type="button" className="secondary" aria-expanded={!collapsed} onClick={onToggle}>{collapsed?'展开':'折叠'}</button>{!finished&&<button type="button" disabled={item.uncertain||item.meta.ready===false} aria-label={`结束${item.meta.label}`} onClick={onCancel}>×</button>}</div></header>
  {item.uncertain&&<p role="alert">接管状态尚未核实，请先核对，暂不重复办理。<button onClick={onRecover}>核对接管状态</button></p>}
  <div hidden={collapsed}><TaskCompletionContext.Provider value={onComplete}><DraftContext.Provider value={item.draft}><Suspense fallback={<p>正在恢复任务表单…</p>}>{node}</Suspense></DraftContext.Provider></TaskCompletionContext.Provider></div>
 </section>;
}

export function NovaPresentationProvider({children}:{children:ReactNode}){
 const [workbench,setWorkbench]=useState<HTMLDivElement|null>(null),[fullscreen,setFullscreen]=useState(false);
 const fullscreenRef=useRef(false);
 const positions=useRef(new Map<HTMLElement,DOMRect>());
 const capture=useCallback(()=>{if(positions.current.size)return;const next=new Map<HTMLElement,DOMRect>();document.querySelectorAll<HTMLElement>('.nova-task-surface,.nova-message').forEach(element=>next.set(element,element.getBoundingClientRect()));positions.current=next;},[]);
 useLayoutEffect(()=>{
  const workspace=workbench?.closest('.home-workspace');if(!workspace)return;
  const media=matchMedia('(min-width:1101px)');
  const update=()=>{const next=media.matches&&workspace.classList.contains('nova-expanded');if(next===fullscreenRef.current)return;capture();fullscreenRef.current=next;setFullscreen(next);};
  const observer=new MutationObserver(update);observer.observe(workspace,{attributes:true,attributeFilter:['class']});media.addEventListener('change',update);update();
  return()=>{observer.disconnect();media.removeEventListener('change',update);};
 },[workbench,capture]);
 useLayoutEffect(()=>{
  const body=workbench?.closest<HTMLElement>('.agent-body');if(!body)return;
  const update=()=>workbench?.style.setProperty('--nova-body-height',`${body.clientHeight-48}px`);
  const observer=new ResizeObserver(update);observer.observe(body);update();return()=>observer.disconnect();
 },[workbench]);
 const [records,setRecords]=useState<Map<string,RecordItem>>(new Map()),[dialog,setDialog]=useState<Dialog|null>(null),[notice,setNotice]=useState(''),[focusVersion,setFocusVersion]=useState(0);
 const recordsRef=useRef(records);recordsRef.current=records;
 const registrations=useRef(new Map<string,symbol>());
 const releaseRequests=useRef(new Map<string,string>());
 const focused=useRef<string|null>(null),lock=useRef(false);
 const confirm=useCallback((title:string,body:string,accept='确认')=>{window.dispatchEvent(new Event('nova-dismiss-menus'));return new Promise<boolean>(resolve=>setDialog({title,body,accept,resolve}));},[]);
 const register=useCallback((item:RecordItem)=>{
  const ticket=Symbol(item.meta.key);registrations.current.set(item.meta.key,ticket);
  const fingerprint=(value:RecordItem)=>JSON.stringify({meta:value.meta,props:isValidElement(value.node)?value.node.props:value.node});
  const previous=recordsRef.current.get(item.meta.key);
  if(!previous||!previous.ended&&fingerprint(previous)!==fingerprint(item))capture();
  setRecords(old=>{const next=new Map(old),prior=next.get(item.meta.key);if(prior?.ended)return old;if(prior&&fingerprint(prior)===fingerprint(item))return old;next.set(item.meta.key,{...item,collapsed:item.meta.finished&&!prior?.meta.finished?true:prior?.collapsed??!!item.meta.finished,completed:prior?.completed&&(!isValidElement(prior.node)||!isValidElement(item.node)||prior.node.key===item.node.key),uncertain:prior?.uncertain,uncertainMode:prior?.uncertainMode,host:prior?.host??item.host,draft:prior?.draft??item.meta.released?.draft??item.draft});return next;});
  return()=>{queueMicrotask(()=>{if(registrations.current.get(item.meta.key)!==ticket)return;setRecords(old=>{const prior=old.get(item.meta.key);if(!prior||prior.anchor!==item.anchor)return old;const next=new Map(old);next.delete(item.meta.key);return next;});});};
 },[]);
 const candidate=(session:string)=>selectHandoffCandidate(recordsRef.current.values(),session,focused.current);
 // 人工接管默认针对最新一个未完成任务，不随焦点改变。
 const latest=(session:string)=>selectHandoffCandidate([...recordsRef.current.values()].sort((a,b)=>a.anchor.compareDocumentPosition(b.anchor)&Node.DOCUMENT_POSITION_PRECEDING?1:-1),session,null);
 // 打开对应页面，并把任务卡已填写的内容交给页面原表单。
 const openManual=(item:RecordItem,draft:Record<string,unknown>,handoff:Record<string,unknown>={})=>{
   if(item.meta.key.startsWith('wealth:')){const store=agentDrafts.get(item.meta.key.slice(7));if(store)manualDrafts.adopt(store);}
   window.dispatchEvent(new CustomEvent('nova-manual-navigate',{detail:{destination:item.meta.destination,service:item.meta.service}}));
   const props=isValidElement(item.node)?(item.node.props as Record<string,unknown>):{};
   offerManualPrefill({key:item.meta.key,draft:{...draft},handoff,task:((props.task??props.card)as Record<string,unknown>|undefined)??{}});
 };
 const finishRelease=(item:RecordItem,mode:'manual'|'cancel',message:string,handoff:Record<string,unknown>={})=>{
   setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:false,ended:true,handedOff:mode==='manual',handoff,receipt:mode==='manual'?'已转人工办理：已填写的内容已带入对应页面的原表单，请在页面核对后继续。NOVA不再接收该任务的后续结果。':message});return next;});
   releaseRequests.current.delete(item.meta.key);
   if(mode==='manual')openManual(item,item.draft,handoff);
   window.dispatchEvent(new Event('nova-clear-verification'));
   window.dispatchEvent(new CustomEvent('nova-task-released',{detail:{session:item.meta.session,key:item.meta.key,message}}));
 };
 const release=async(item:RecordItem,mode:'manual'|'cancel')=>{
  if(lock.current)return;lock.current=true;
  try{
   if(item.meta.key.startsWith('wealth:'))await flushNovaWealthView(item.meta.key.slice(7));
   const requestId=releaseRequests.current.get(item.meta.key)??crypto.randomUUID();releaseRequests.current.set(item.meta.key,requestId);
   const {data}=await apiClient.post<{message:string;handoff?:Record<string,unknown>}>(`/nova/presentation/${item.meta.session}/release`,{key:item.meta.key,mode,request_id:requestId,draft:item.draft});
   finishRelease(item,mode,data.message,data.handoff);
  }catch(error){
   try{const {data}=await apiClient.get<(TaskMeta&{released?:{mode:string;message:string;request_id:string;handoff?:Record<string,unknown>}})[]>(`/nova/presentation/${item.meta.session}`);const receipt=data.find(value=>value.key===item.meta.key)?.released;
    if(receipt&&receipt.request_id===releaseRequests.current.get(item.meta.key)){finishRelease(item,receipt.mode as 'manual'|'cancel',receipt.message,receipt.handoff);return;}
    if(receipt){setNotice('任务已在其他页面处理，请重新打开对话核对。');return;}
    const status=(error as {response?:{status?:number}}).response?.status;
    if(status&&status>=400&&status<500){releaseRequests.current.delete(item.meta.key);setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:false});return next;});}
    else setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:true,uncertainMode:mode});return next;});
   }catch{setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:true,uncertainMode:mode});return next;});}
   const detail=(error as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail;setNotice(detail?.message??'接管结果未确认，请核对原任务后重试，未重新提交业务。');
  }
  finally{lock.current=false;}
 };
 const handoff=async(session:string)=>{const item=latest(session);if(!item)return;await release(item,'manual');};
 const flush=async(session:string)=>{const item=candidate(session);if(item&&!item.meta.key.startsWith('wealth:'))await apiClient.post(`/nova/presentation/${session}/draft`,{key:item.meta.key,draft:item.draft});};
 const resume=(key:string)=>{const item=recordsRef.current.get(key);if(!item||!item.handedOff&&item.meta.released?.mode!=='manual')return;openManual(item,item.meta.released?.draft??item.draft,item.handoff??item.meta.released?.handoff);};
 useEffect(()=>{
  const owner=()=>{setRecords(new Map());clearManualPrefill();releaseRequests.current.clear();focused.current=null;setDialog(previous=>{previous?.resolve(false);return null;});};
  window.addEventListener('nova-owner-change',owner);return()=>window.removeEventListener('nova-owner-change',owner);
 },[]);
 const changeCard=(key:string,complete=false)=>{capture();setRecords(old=>{const item=old.get(key);if(!item||complete&&(item.meta.finished||item.completed))return old;const next=new Map(old);next.set(key,{...item,completed:complete||item.completed,collapsed:complete||!item.collapsed});return next;});};
 useLayoutEffect(()=>{
  let count=0;
  for(const item of records.values()){
   const detached=fullscreen&&!item.collapsed&&!item.ended&&!item.meta.released;
   const target=detached?workbench:item.anchor;
   if(detached)count++;
   item.anchor.classList.toggle('is-detached',detached);
   if(target&&item.host.parentElement!==target){const active=document.activeElement as HTMLElement|null;const restore=active&&item.host.contains(active);target.appendChild(item.host);if(restore)active.focus({preventScroll:true});}
  }
  workbench?.parentElement?.classList.toggle('has-open-tasks',count>0);
  if(!matchMedia('(prefers-reduced-motion: reduce)').matches)positions.current.forEach((before,element)=>{
   if(!element.isConnected)return;const after=element.getBoundingClientRect();
   if(!before.width||!after.width||Math.abs(before.x-after.x)+Math.abs(before.y-after.y)<1)return;
   element.getAnimations().forEach(animation=>animation.cancel());
   element.animate([{transform:`translate(${before.x-after.x}px,${before.y-after.y}px)`},{transform:'translate(0,0)'}],{duration:380,easing:'cubic-bezier(.333333,1,.666667,1)'});
  });
  positions.current.clear();
 },[records,fullscreen,workbench]);
 const value=useMemo(()=>({register,setWorkbench,confirm,handoff,flush,resume,canHandoff:(session:string)=>!!latest(session)}),[records,register,confirm,focusVersion]);
 return <Context.Provider value={value}>{children}
  {[...records.values()].map(item=>createPortal(<LiveTask item={item} onToggle={()=>changeCard(item.meta.key)} onComplete={()=>changeCard(item.meta.key,true)} onRecover={()=>void release(item,item.uncertainMode??'manual')} onResume={()=>resume(item.meta.key)} onFocus={()=>{if(focused.current!==item.meta.key){focused.current=item.meta.key;setFocusVersion(v=>v+1);}}} onCancel={()=>void confirm('结束当前任务？','结束尚未提交的Agent任务及后续队列；已受理、已成交操作不会撤销。','确认结束').then(yes=>{if(yes)void release(item,'cancel');})}/>,item.host,item.meta.key))}
  {dialog&&<Modal className="nova-confirm-overlay"><section role="dialog" aria-modal="true" aria-labelledby="nova-confirm-title" tabIndex={-1} ref={element=>element?.focus()} onKeyDown={event=>{if(event.key==='Escape'){dialog.resolve(false);setDialog(null);}if(event.key==='Tab'){const controls=event.currentTarget.querySelectorAll<HTMLButtonElement>('button');event.preventDefault();(document.activeElement===controls[0]&&!event.shiftKey?controls[1]:controls[0])?.focus();}}}><h3 id="nova-confirm-title">{dialog.title}</h3><p>{dialog.body}</p><footer><button type="button" className="secondary" onClick={()=>{dialog.resolve(false);setDialog(null);}}>取消</button><button type="button" onClick={()=>{dialog.resolve(true);setDialog(null);}}>{dialog.accept}</button></footer></section></Modal>}
  {notice&&<Modal className="nova-confirm-overlay"><section role="alertdialog"><h3>暂未完成</h3><p>{notice}</p><button onClick={()=>setNotice('')}>知道了</button></section></Modal>}
 </Context.Provider>;
}

export function NovaTaskSlot({meta,children}:{meta:TaskMeta;children:ReactNode}){
 const {register}=useNovaPresentation(),anchor=useRef<HTMLDivElement>(null),host=useRef<HTMLDivElement>(),draft=useRef<Record<string,unknown>>({});
 if(!host.current){host.current=document.createElement('div');host.current.className='nova-task-host';}
 useLayoutEffect(()=>{if(!anchor.current)return;return register({meta,node:children,host:host.current!,anchor:anchor.current,draft:draft.current});},[meta.key,meta.session,meta.finished,meta.released,children,register]);
 return <div className="nova-task-anchor" ref={anchor}/>;
}
