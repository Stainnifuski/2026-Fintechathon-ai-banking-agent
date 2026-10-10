import type {CandidateGroup,PortfolioPlan} from './wealth';
import type {ProductSelectionState} from '../components/Wealth/productSelection';

export interface ManualDraft {
  mode:string; category:string; theme:string; pageSize:number;
  selected:{type:'orders'|'positions';ids:string[]}; inputMode:string; value:string;
  target:string; period:string; date:string;
  candidateGroups:CandidateGroup[]; candidateSelection:ProductSelectionState;
  portfolio:PortfolioPlan|null; ratios:{planKey:string;values:Record<string,string>}|null;
}
export const draftScope=(owner:string,context:string,accounts:string[])=>JSON.stringify([owner,context,[...accounts].sort()]);
const initial=(context:string):ManualDraft=>({mode:'general',category:'bond_fund',theme:'high_dividend',pageSize:5,selected:{type:'positions',ids:[]},inputMode:'market_value',value:'',target:'',period:'day',date:'',candidateGroups:[],candidateSelection:{scopeKey:context,items:[],preferredShares:{}},portfolio:null,ratios:null});
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
}
export const manualDrafts=new ManualDraftStore();
