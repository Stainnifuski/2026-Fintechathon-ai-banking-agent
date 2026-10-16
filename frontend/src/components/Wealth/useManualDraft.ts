import {useSyncExternalStore} from 'react';
import {manualDrafts} from '../../services/wealthManualDraft';
import type {ManualDraft} from '../../services/wealthManualDraft';
export function useManualDraft(key:string|null,context:string){
  const draft=useSyncExternalStore(manualDrafts.subscribe,()=>manualDrafts.read(key,context));
  const field=<K extends keyof ManualDraft>(name:K):[ManualDraft[K],(next:ManualDraft[K]|((previous:ManualDraft[K])=>ManualDraft[K]))=>void]=>[draft[name],next=>manualDrafts.update(key,name,next)];
  return field;
}
