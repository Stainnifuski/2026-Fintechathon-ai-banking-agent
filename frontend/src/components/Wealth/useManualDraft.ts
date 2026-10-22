import {useSyncExternalStore} from 'react';
import {manualDrafts,ManualDraftStore} from '../../services/wealthManualDraft';
import type {ManualDraft} from '../../services/wealthManualDraft';
export function useManualDraft(key:string|null,context:string,store:ManualDraftStore=manualDrafts){
  const draft=useSyncExternalStore(store.subscribe,()=>store.read(key,context));
  const field=<K extends keyof ManualDraft>(name:K):[ManualDraft[K],(next:ManualDraft[K]|((previous:ManualDraft[K])=>ManualDraft[K]))=>void]=>[draft[name],next=>store.update(key,name,next)];
  return field;
}
