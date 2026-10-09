import {useEffect,useMemo,useRef,useSyncExternalStore} from 'react';
import {get} from '../../services/wealth';
import type {AssetSummary} from '../../services/wealth';
import {AssetSummaryStore} from '../../services/assetSummaryStore';
import {subscribeAssetSummaryChanges} from '../../services/assetSummaryEvents';

export function useAssetSummary(userId:string|null,revision:number,page:string|null){
  const store=useMemo(()=>new AssetSummaryStore(()=>get<AssetSummary>('/asset-summary')),[]);
  const previousRevision=useRef(revision);
  const snapshot=useSyncExternalStore(store.subscribe,store.getSnapshot);
  useEffect(()=>{store.setOwner(userId);return()=>store.setOwner(null);},[store,userId]);
  useEffect(()=>{
    if(previousRevision.current!==revision){store.invalidate();previousRevision.current=revision;}
    if(!userId)return;
    if(page)void store.refresh(userId);
    const unsubscribe=subscribeAssetSummaryChanges(()=>{store.invalidate();if(page)void store.refresh(userId);});
    const timer=page?window.setInterval(()=>void store.refresh(userId),5000):null;
    return()=>{unsubscribe();if(timer!==null)window.clearInterval(timer);};
  },[store,userId,revision,page]);
  const state=snapshot.owner===userId?snapshot:{owner:userId,data:null,loading:!!userId,error:''};
  return {...state,refresh:()=>{if(userId)void store.refresh(userId);}};
}
