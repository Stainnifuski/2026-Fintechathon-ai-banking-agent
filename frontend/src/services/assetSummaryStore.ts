import type {AssetSummary} from './wealth';

export interface AssetSummaryState {owner:string|null;data:AssetSummary|null;loading:boolean;error:string}
const empty=(owner:string|null):AssetSummaryState=>({owner,data:null,loading:false,error:''});

/** Both pages consume this single owner-bound cache; late responses cannot restore an old scope. */
export class AssetSummaryStore {
  private state=empty(null);
  private generation=0;
  private pending:Promise<void>|null=null;
  private listeners=new Set<()=>void>();
  private read:()=>Promise<AssetSummary>;
  constructor(read:()=>Promise<AssetSummary>){this.read=read;}
  getSnapshot=()=>this.state;
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  private publish(state:AssetSummaryState){this.state=state;for(const notify of this.listeners)notify();}
  setOwner(owner:string|null){if(owner!==this.state.owner){this.generation++;this.pending=null;this.publish(empty(owner));}}
  invalidate(){this.generation++;this.pending=null;this.publish(empty(this.state.owner));}
  refresh(owner:string):Promise<void>{
    this.setOwner(owner);
    if(this.pending)return this.pending;
    const generation=++this.generation;
    this.publish({...this.state,loading:true,error:''});
    const request=this.read().then(data=>{
      if(generation===this.generation)this.publish({owner,data,loading:false,error:''});
    }).catch((error:unknown)=>{
      if(generation===this.generation)this.publish({owner,data:null,loading:false,error:(error as {response?:{status?:number}})?.response?.status===401?'登录已过期，请重新登录。':'总资产查询失败，请刷新重试。'});
    }).finally(()=>{if(generation===this.generation)this.pending=null;});
    this.pending=request;
    return request;
  }
}
