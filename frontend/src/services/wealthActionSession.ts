import type {ActionName,ActionRequest,ActionResult} from './wealthActions';

/** One owner-scoped panel session, shared by controls and detail/comparison views. */
export class WealthActionSession {
  private context:string|null=null;
  private generation=0;
  private session:{id:string;version:number}|null=null;
  private tail:Promise<unknown>=Promise.resolve();
  private send:(request:ActionRequest)=>Promise<ActionResult>;
  constructor(send:(request:ActionRequest)=>Promise<ActionResult>){this.send=send;}
  setContext(context:string|null){
    if(context===this.context)return;
    this.context=context;this.generation++;this.session=null;
  }
  request(action:ActionName,params:Record<string,unknown>,selection?:ActionRequest['selection_context']){
    const context=this.context,generation=this.generation;
    const current=()=>context!==null&&context===this.context&&generation===this.generation;
    const response=this.tail.then(async()=>{
      if(!current())throw new Error('资金范围已变化，请重新查询。');
      const session=this.session;
      const result=await this.send({schema_version:'wealth-action-v1',request_id:'WA'+crypto.randomUUID().replace(/-/g,''),context_id:context!,
        ...(session?{session_id:session.id,expected_session_version:session.version}:{}),
        ...(selection?.ids.length?{selection_context:selection}:{}),action,params});
      if(!current()||result.context_id!==context)throw new Error('资金范围已变化，请重新查询。');
      this.session={id:result.session_id,version:result.session_version};
      return result;
    });
    // A failed request does not strand subsequent reads; never automatically retry a preview.
    this.tail=response.catch(()=>undefined);
    return response;
  }
}
