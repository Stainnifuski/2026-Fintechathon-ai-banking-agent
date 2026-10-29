/** Shared invalidation signal; contains no bank data or authentication state. */
const listeners=new Set<()=>void>();
export function invalidateAssetSummary(){for(const notify of listeners)notify();}
export function subscribeAssetSummaryChanges(notify:()=>void){listeners.add(notify);return()=>{listeners.delete(notify);};}
export function changesAssetFacts(method:string,url:string):boolean {
  if(!['post','put','patch','delete'].includes(method.toLowerCase()))return false;
  return /^\/wealth\/(contexts\/select|previews\/[^/]+\/execute|orders\/[^/]+\/cancel|plans\/[^/]+\/(execute|progress)|tasks\/[^/]+\/progress)(?:\?|$)/.test(url);
}
