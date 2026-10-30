import {useEffect,useRef} from 'react';

/** 人工接管：把聊天任务卡里已填写的内容交给对应页面的原表单。只在内存中保留，不含验证码或授权。 */
/** handoff：服务端在接管时交回的本人收款信息（卡片上仅显示脱敏号码）。 */
export type ManualPrefill={key:string;draft:Record<string,unknown>;task:Record<string,unknown>;handoff?:Record<string,unknown>};
const pending=new Map<string,ManualPrefill>();
const kindOf=(key:string)=>key.slice(0,key.indexOf(':'));

export const offerManualPrefill=(value:ManualPrefill)=>{
  const kind=kindOf(value.key);pending.set(kind,value);
  window.dispatchEvent(new CustomEvent('nova-manual-prefill',{detail:kind}));
};
export const clearManualPrefill=()=>pending.clear();

/** 页面表单消费待填内容；apply 返回 false 表示数据未就绪，稍后（ready 变化时）重试。 */
export function useManualPrefill(kinds:string[],apply:(value:ManualPrefill)=>boolean|void,ready=true){
  const callback=useRef(apply);callback.current=apply;
  const kindKey=kinds.join(',');
  useEffect(()=>{
    const consume=()=>{
      if(!ready)return;
      for(const kind of kindKey.split(',')){
        const value=pending.get(kind);
        if(value&&callback.current(value)!==false&&pending.get(kind)===value)pending.delete(kind);
      }
    };
    consume();
    window.addEventListener('nova-manual-prefill',consume);
    return()=>window.removeEventListener('nova-manual-prefill',consume);
  },[kindKey,ready]);
}
