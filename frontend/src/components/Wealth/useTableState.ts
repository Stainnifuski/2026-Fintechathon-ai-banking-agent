import {useEffect,useState} from 'react';
import {pageRows,togglePage} from './tableData';

/** Each table owns an instance; a scope change invalidates old IDs immediately. */
export function useTableState<T>(rows:T[],scope:string,id:(row:T)=>string){
  const [state,setState]=useState({scope,page:1,pageSize:5,selected:[] as string[]});
  const current=state.scope===scope?state:{scope,page:1,pageSize:5,selected:[] as string[]};
  const available=new Set(rows.map(id));
  const selected=current.selected.filter(key=>available.has(key));
  const paged=pageRows(rows,current.page,current.pageSize);const visibleIds=paged.rows.map(id);
  const availableKey=JSON.stringify([...available].sort());
  useEffect(()=>{setState(s=>{
    if(s.scope!==scope)return {scope,page:1,pageSize:5,selected:[]};
    const remaining=s.selected.filter(key=>available.has(key));
    return remaining.length===s.selected.length?s:{...s,selected:remaining};
  });},[scope,availableKey]);
  const update=(patch:Partial<typeof current>)=>setState({...current,...patch});
  return {selected,...paged,pageSize:current.pageSize,allChecked:visibleIds.length>0&&visibleIds.every(key=>selected.includes(key)),
    changePage:(page:number)=>update({page}),changeSize:(pageSize:number)=>update({page:1,pageSize}),
    toggle:(key:string,checked:boolean)=>update({selected:togglePage(selected,[key],checked)}),
    toggleVisible:(checked:boolean)=>update({selected:togglePage(selected,visibleIds,checked)}),clear:()=>update({selected:[]})};
}
