import type {CandidateGroup,PortfolioPlan} from './wealth';
import type {ProductSelectionState} from '../components/Wealth/productSelection';
import {initialTableState} from '../components/Wealth/tableState.ts';
import type {TableState} from '../components/Wealth/tableState';

export interface ManualDraft {
  orderTable:TableState;positionTable:TableState;
  mode:string; category:string; nameQuery:string; theme:string;
  workflow:'planning'|'target';
  inputMode:string; value:string;
  target:string; period:string; date:string;
  candidateGroups:CandidateGroup[]; candidateSelection:ProductSelectionState;
  portfolio:PortfolioPlan|null; ratios:{planKey:string;values:Record<string,string>}|null;
}
export const draftScope=(owner:string,context:string,accounts:string[])=>JSON.stringify([owner,context,[...accounts].sort()]);
const initial=(context:string):ManualDraft=>({orderTable:initialTableState(),positionTable:initialTableState(),workflow:'planning',mode:'category',category:'bond_fund',nameQuery:'',theme:'high_dividend',inputMode:'market_value',value:'',target:'',period:'day',date:'',candidateGroups:[],candidateSelection:{scopeKey:context,items:[],preferredShares:{}},portfolio:null,ratios:null});
/** Memory-only inputs. Never store previews, consent, codes, tokens or action sessions. */
export class ManualDraftStore {
  private currentKey:string|null=null;
  private value:ManualDraft=initial('');
  private listeners=new Set<()=>void>();
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  bind=(key:string,context:string)=>{
    if(this.currentKey===key)return;
    this.currentKey=key;this.value=initial(context);this.listeners.forEach(listener=>listener());
  };
  read=(key:string|null,_context:string):ManualDraft=>key!==null&&key===this.currentKey?this.value:this.empty;
  private empty=initial('');
  update<K extends keyof ManualDraft>(key:string|null,field:K,next:ManualDraft[K]|((previous:ManualDraft[K])=>ManualDraft[K])){
    if(key===null||key!==this.currentKey)return;
    const value=typeof next==='function'?(next as (previous:ManualDraft[K])=>ManualDraft[K])(this.value[field]):next;
    this.value={...this.value,[field]:value};this.listeners.forEach(listener=>listener());
  }
  clear=()=>{this.currentKey=null;this.value=initial('');this.listeners.forEach(listener=>listener());};
  /** 人工接管：页面表单沿用聊天理财卡已填写的输入。 */
  adopt=(source:ManualDraftStore)=>{
    if(source.currentKey===null)return;
    this.currentKey=source.currentKey;this.value=source.value;this.listeners.forEach(listener=>listener());
  };
}
export const manualDrafts=new ManualDraftStore();
/** 聊天中各理财任务卡自己的输入草稿，按理财任务编号索引，仅供人工接管时移交。 */
export const agentDrafts=new Map<string,ManualDraftStore>();
