import Modal from './Modal';
import {createContext,useContext,useState,useRef,useLayoutEffect,useEffect,useCallback,useMemo,cloneElement,isValidElement,Suspense,type ReactNode,type ReactElement,type Dispatch,type SetStateAction} from 'react';
import {createPortal} from 'react-dom';
import {apiClient} from '../services/api';
import {flushNovaWealthView} from '../services/nova';
import {selectHandoffCandidate} from './novaHandoffCandidate';
import './novaTaskPresentation.css';

export type TaskMeta={key:string;session:string;label:string;destination:string;ready?:boolean;blocked?:boolean;service?:'flower'|'cake';finished?:boolean;summary?:string;released?:{mode:string;message:string;draft?:Record<string,unknown>}|null};
type RecordItem={meta:TaskMeta;node:ReactNode;host:HTMLDivElement;anchor:HTMLElement;collapsed?:boolean;completed?:boolean;manual?:boolean;ended?:boolean;uncertain?:boolean;uncertainMode?:'manual'|'cancel';receipt?:string;draft:Record<string,unknown>};
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
type Context={register:(item:RecordItem)=>()=>void;setWorkbench:(target:HTMLDivElement|null)=>void;confirm:(title:string,body:string,accept?:string)=>Promise<boolean>;handoff:(session:string)=>Promise<void>;flush:(session:string)=>Promise<void>;resume:(key:string)=>void;canHandoff:(session:string)=>boolean;manual:RecordItem[]};
const Context=createContext<Context|null>(null);
const TargetContext=createContext<Map<string,HTMLElement>|null>(null);
export const useNovaPresentation=()=>{const value=useContext(Context);if(!value)throw new Error('Missing NOVA presentation provider');return value;};

function LiveTask({item,onToggle,onComplete,onCancel,onFocus,onRecover}:{item:RecordItem;onToggle:()=>void;onComplete:()=>void;onCancel:()=>void;onFocus:()=>void;onRecover:()=>void}){
 const collapsed=!!item.collapsed,finished=!!item.meta.finished||!!item.completed;
 const [manualNode,setManualNode]=useState<ReactNode>(null),[manualError,setManualError]=useState('');
 const original=useRef(item.node);original.current=item.node;
 // Retain the very same mounted component when ownership moves to a workspace.
 // Only its callbacks change; local edits and selected products remain mounted.
 const manualUpdated=(updated:unknown)=>{
  setManualNode(previous=>{
   const node=(previous??original.current) as ReactElement<Record<string,unknown>>;
   if(!isValidElement(node))return node;
   const props=node.props, value=updated as Record<string,unknown>;
   if(item.meta.key.startsWith('wealth:'))return cloneElement(node,{card:(updated as {task_id:string}[]).find(x=>x.task_id===item.meta.key.slice(7))??props.card});
   if(item.meta.key.startsWith('bill:'))return cloneElement(node,{card:updated});
   if(item.meta.key.startsWith('collection:'))return cloneElement(node,{session:{...(props.session as object),...value},card:(value.collection_cards as {collection_id:string}[]|undefined)?.find(x=>'collection:'+x.collection_id===item.meta.key)??props.card});
   if(item.meta.key.startsWith('transfer:'))return cloneElement(node,{session:updated,task:(value.tasks as {chat_task_id:string}[]).find(x=>'transfer:'+x.chat_task_id===item.meta.key)??props.task});
   return cloneElement(node,{task:(updated as {entry_id:string}[]).find(x=>item.meta.key.endsWith(':'+x.entry_id))??props.task});
  });
 };
 let node=item.manual?(manualNode??item.node):item.node;
 if(isValidElement(node)){
  const props=(node as ReactElement<Record<string,unknown>>).props;
  node=cloneElement(node as ReactElement<Record<string,unknown>>,item.manual?{manual:true,onUpdated:manualUpdated,onFocus:()=>{},disabled:false,onSelectCandidate:(candidateId:string)=>{setManualError('');void apiClient.post(`/nova/presentation/${item.meta.session}/candidate`,{task_id:item.meta.key.slice(9),candidate_id:candidateId}).then(({data})=>manualUpdated(data)).catch(()=>setManualError('候选可能已过期，请在工作台重新核对原收款信息；尚未转账。'));}}:{disabled:!!props.disabled||!!item.uncertain||item.meta.ready===false||!!item.meta.blocked,onUpdated:(updated:unknown)=>{(props.onUpdated as ((value:unknown)=>void)|undefined)?.(updated);window.dispatchEvent(new CustomEvent('nova-task-updated',{detail:{key:item.meta.key}}));}});
 }
 if(item.ended||item.meta.released&&!item.manual)return <section className="nova-task-summary" role="status"><strong>{item.meta.label}</strong><p>{item.receipt??item.meta.released?.message??'任务已结束；已有银行记录保留。'}</p></section>;
 return <section className={`nova-task-surface${item.manual?' is-manual':''}`} data-nova-task={item.meta.key} onFocusCapture={onFocus} onPointerDown={onFocus}>
  <header className="nova-task-header"><div><h3>{item.meta.label}</h3><small>{item.manual?'已人工接管 · 在工作台继续':item.completed?'已完成':item.meta.summary??(finished?'已完成':'待本人办理')}</small></div><div className="nova-task-controls"><button type="button" className="secondary" aria-expanded={!collapsed} onClick={onToggle}>{collapsed?'展开':'折叠'}</button>{!item.manual&&!finished&&<button type="button" disabled={item.uncertain||item.meta.ready===false} aria-label={`结束${item.meta.label}`} onClick={onCancel}>×</button>}</div></header>
  {manualError&&<p role="alert">{manualError}</p>}
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
 const focused=useRef<string|null>(null),lock=useRef(false),manualTargets=useRef(new Map<string,HTMLElement>());
 const confirm=useCallback((title:string,body:string,accept='确认')=>{window.dispatchEvent(new Event('nova-dismiss-menus'));return new Promise<boolean>(resolve=>setDialog({title,body,accept,resolve}));},[]);
 const register=useCallback((item:RecordItem)=>{
  const ticket=Symbol(item.meta.key);registrations.current.set(item.meta.key,ticket);
  const fingerprint=(value:RecordItem)=>JSON.stringify({meta:value.meta,props:isValidElement(value.node)?value.node.props:value.node});
  const previous=recordsRef.current.get(item.meta.key);
  if(!previous||!previous.manual&&!previous.ended&&fingerprint(previous)!==fingerprint(item))capture();
  setRecords(old=>{const next=new Map(old),prior=next.get(item.meta.key);if(prior?.manual||prior?.ended)return old;if(prior&&fingerprint(prior)===fingerprint(item))return old;next.set(item.meta.key,{...item,collapsed:item.meta.finished&&!prior?.meta.finished?true:prior?.collapsed??!!item.meta.finished,completed:prior?.completed&&(!isValidElement(prior.node)||!isValidElement(item.node)||prior.node.key===item.node.key),uncertain:prior?.uncertain,uncertainMode:prior?.uncertainMode,host:prior?.host??item.host,draft:prior?.draft??item.meta.released?.draft??item.draft});return next;});
  return()=>{queueMicrotask(()=>{if(registrations.current.get(item.meta.key)!==ticket)return;setRecords(old=>{const prior=old.get(item.meta.key);if(!prior||prior.manual||prior.anchor!==item.anchor)return old;const next=new Map(old);next.delete(item.meta.key);return next;});});};
 },[]);
 const candidate=(session:string)=>selectHandoffCandidate(recordsRef.current.values(),session,focused.current);
 const finishRelease=(item:RecordItem,mode:'manual'|'cancel',message:string)=>{
   setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:false,manual:mode==='manual',ended:mode==='cancel',receipt:message});return next;});
   releaseRequests.current.delete(item.meta.key);
   if(mode==='manual')window.dispatchEvent(new CustomEvent('nova-manual-navigate',{detail:{destination:item.meta.destination,service:item.meta.service}}));
   window.dispatchEvent(new Event('nova-clear-verification'));
   window.dispatchEvent(new CustomEvent('nova-task-released',{detail:{session:item.meta.session,key:item.meta.key,message}}));
 };
 const release=async(item:RecordItem,mode:'manual'|'cancel')=>{
  if(lock.current)return;lock.current=true;
  try{
   if(mode==='manual'&&!manualTargets.current.get(item.meta.destination))throw new Error('对应工作台尚未准备好，原任务保持。');
   if(item.meta.key.startsWith('wealth:'))await flushNovaWealthView(item.meta.key.slice(7));
   const requestId=releaseRequests.current.get(item.meta.key)??crypto.randomUUID();releaseRequests.current.set(item.meta.key,requestId);
   const {data}=await apiClient.post<{message:string}>(`/nova/presentation/${item.meta.session}/release`,{key:item.meta.key,mode,request_id:requestId,draft:item.draft});
   finishRelease(item,mode,data.message);
  }catch(error){
   try{const {data}=await apiClient.get<(TaskMeta&{released?:{mode:string;message:string;request_id:string}})[]>(`/nova/presentation/${item.meta.session}`);const receipt=data.find(value=>value.key===item.meta.key)?.released;
    if(receipt&&receipt.request_id===releaseRequests.current.get(item.meta.key)){finishRelease(item,receipt.mode as 'manual'|'cancel',receipt.message);return;}
    if(receipt){setNotice('任务已在其他页面处理，请重新打开对话核对。');return;}
    const status=(error as {response?:{status?:number}}).response?.status;
    if(status&&status>=400&&status<500){releaseRequests.current.delete(item.meta.key);setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:false});return next;});}
    else setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:true,uncertainMode:mode});return next;});
   }catch{setRecords(old=>{const next=new Map(old);next.set(item.meta.key,{...item,uncertain:true,uncertainMode:mode});return next;});}
   const detail=(error as {response?:{data?:{detail?:{message?:string}}}}).response?.data?.detail;setNotice(detail?.message??'接管结果未确认，请核对原任务后重试，未重新提交业务。');
  }
  finally{lock.current=false;}
 };
 const handoff=async(session:string)=>{const item=candidate(session);if(!item)return;await release(item,'manual');};
 const flush=async(session:string)=>{const item=candidate(session);if(item&&!item.meta.key.startsWith('wealth:'))await apiClient.post(`/nova/presentation/${session}/draft`,{key:item.meta.key,draft:item.draft});};
 const resume=(key:string)=>{const item=recordsRef.current.get(key);if(!item||item.meta.released?.mode!=='manual')return;setRecords(old=>{const next=new Map(old);next.set(key,{...item,manual:true,draft:item.meta.released?.draft??item.draft});return next;});window.dispatchEvent(new CustomEvent('nova-manual-navigate',{detail:{destination:item.meta.destination,service:item.meta.service}}));};
 useEffect(()=>{
  const owner=()=>{setRecords(new Map());releaseRequests.current.clear();focused.current=null;setDialog(previous=>{previous?.resolve(false);return null;});};
  window.addEventListener('nova-owner-change',owner);return()=>window.removeEventListener('nova-owner-change',owner);
 },[]);
 const changeCard=(key:string,complete=false)=>{capture();setRecords(old=>{const item=old.get(key);if(!item||complete&&(item.meta.finished||item.completed))return old;const next=new Map(old);next.set(key,{...item,completed:complete||item.completed,collapsed:complete||!item.collapsed});return next;});};
 useLayoutEffect(()=>{
  let count=0;
  for(const item of records.values()){
   const detached=fullscreen&&!item.collapsed&&!item.manual&&!item.ended&&!item.meta.released;
   const target=item.manual?manualTargets.current.get(item.meta.destination):detached?workbench:item.anchor;
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
 const value=useMemo(()=>({register,setWorkbench,confirm,handoff,flush,resume,canHandoff:(session:string)=>!!candidate(session),manual:[...records.values()].filter(x=>x.manual)}),[records,register,confirm,focusVersion]);
 return <TargetContext.Provider value={manualTargets.current}><Context.Provider value={value}>{children}
  {[...records.values()].map(item=>createPortal(<LiveTask item={item} onToggle={()=>changeCard(item.meta.key)} onComplete={()=>changeCard(item.meta.key,true)} onRecover={()=>void release(item,item.uncertainMode??'manual')} onFocus={()=>{if(focused.current!==item.meta.key){focused.current=item.meta.key;setFocusVersion(v=>v+1);}}} onCancel={()=>void confirm('结束当前任务？','结束尚未提交的Agent任务及后续队列；已受理、已成交操作不会撤销。','确认结束').then(yes=>{if(yes)void release(item,'cancel');})}/>,item.host,item.meta.key))}
  <div hidden aria-hidden="true">{['transfer','wealth','cards','subscriptions','calendar','thirdParty','insights'].map(destination=><div key={destination} ref={element=>{if(element&&!manualTargets.current.has(destination))manualTargets.current.set(destination,element);}} data-manual-fallback={destination}/>)}</div>
  {dialog&&<Modal className="nova-confirm-overlay"><section role="dialog" aria-modal="true" aria-labelledby="nova-confirm-title" tabIndex={-1} ref={element=>element?.focus()} onKeyDown={event=>{if(event.key==='Escape'){dialog.resolve(false);setDialog(null);}if(event.key==='Tab'){const controls=event.currentTarget.querySelectorAll<HTMLButtonElement>('button');event.preventDefault();(document.activeElement===controls[0]&&!event.shiftKey?controls[1]:controls[0])?.focus();}}}><h3 id="nova-confirm-title">{dialog.title}</h3><p>{dialog.body}</p><footer><button type="button" className="secondary" onClick={()=>{dialog.resolve(false);setDialog(null);}}>取消</button><button type="button" onClick={()=>{dialog.resolve(true);setDialog(null);}}>{dialog.accept}</button></footer></section></Modal>}
  {notice&&<Modal className="nova-confirm-overlay"><section role="alertdialog"><h3>暂未完成</h3><p>{notice}</p><button onClick={()=>setNotice('')}>知道了</button></section></Modal>}
 </Context.Provider></TargetContext.Provider>;
}

// Workspace anchors stay mounted across navigation; moving a host does not
// recreate the bank form or replay its side effects.
export function ManualTaskWorkspace({destination}:{destination:string}){
 const context=useNovaPresentation(),targets=useContext(TargetContext),root=useRef<HTMLDivElement>(null);
 const manual=context.manual.filter(x=>x.meta.destination===destination);
 useLayoutEffect(()=>{if(!root.current||!targets)return;targets.set(destination,root.current);for(const item of manual)if(item.host.parentElement!==root.current)root.current.appendChild(item.host);},[destination,context.manual,targets]);
 return <section hidden={!manual.length} className="nova-manual-workspace"><h2>人工接管</h2><p>保留原输入和操作编号，在此手动继续。NOVA已结束原任务，不再接收后续办理结果。</p><div ref={root}/></section>;
}

export function NovaTaskSlot({meta,children}:{meta:TaskMeta;children:ReactNode}){
 const {register,resume,manual}=useNovaPresentation(),anchor=useRef<HTMLDivElement>(null),host=useRef<HTMLDivElement>(),draft=useRef<Record<string,unknown>>({});
 if(!host.current){host.current=document.createElement('div');host.current.className='nova-task-host';}
 useLayoutEffect(()=>{if(!anchor.current)return;return register({meta,node:children,host:host.current!,anchor:anchor.current,draft:draft.current});},[meta.key,meta.session,meta.finished,meta.released,children,register]);
 const detached=manual.find(item=>item.meta.key===meta.key);
 return <><div className="nova-task-anchor" ref={anchor}/>{detached&&<section className="nova-task-summary"><strong>{meta.label} · 已人工接管</strong></section>}{!detached&&!meta.finished&&meta.released?.mode==='manual'&&<button className="secondary" onClick={()=>resume(meta.key)}>继续人工办理</button>}</>;
}
