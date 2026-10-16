import {pageRows,togglePage} from './tableData.ts';

export interface TableState {scope:string;page:number;pageSize:number;selected:string[]}
export interface TableControl {state:TableState;onChange:(state:TableState)=>void}
export const initialTableState=(scope=''):TableState=>({scope,page:1,pageSize:5,selected:[]});
export function currentTableState<T>(state:TableState,scope:string,rows:T[],id:(row:T)=>string):TableState{
  if(state.scope!==scope)return initialTableState(scope);
  const available=new Set(rows.map(id));
  return {...state,page:pageRows(rows,state.page,state.pageSize).page,selected:state.selected.filter(key=>available.has(key))};
}
export function selectTablePage<T>(state:TableState,rows:T[],id:(row:T)=>string,checked:boolean):TableState{
  return {...state,selected:togglePage(state.selected,pageRows(rows,state.page,state.pageSize).rows.map(id),checked)};
}
