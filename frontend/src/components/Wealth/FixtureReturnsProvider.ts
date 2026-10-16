// 只由DEV入口导入：录制结果选择／聚合不构成银行历史收益算法。
import recording from '../../../../tests/fixtures/wealth_portfolio_contract_v1/returns_preview.json' with {type:'json'};
import type {ReturnPoint,ReturnsQuery,WealthReturns,ReturnScenario} from '../../services/wealthReturns.ts';

type Amount=number|null;
const sum=(values:Amount[]):Amount=>values.some(value=>value===null)?null:values.reduce<number>((total,value)=>total+(value??0),0);
const dateOf=(value:Date)=>value.toISOString().slice(0,10);
const nextDay=(value:string)=>dateOf(new Date(Date.parse(`${value}T00:00:00Z`)+86400000));
const endMonth=(value:string)=>{const [year,month]=value.split('-').map(Number);return dateOf(new Date(Date.UTC(year,month,0)));};
const recordedDay=recording.as_of.slice(0,10);
export const returnsScenarios:ReturnScenario[]=recording.scenarios.map(({id,label})=>({id,label}));
export const returnsPreviewNotice=recording.notice;
export const returnsScenarioNotice=(id:string)=>recording.scenarios.find(item=>item.id===id)?.notice??'';

export function recordedReturns(query:ReturnsQuery,scenarioId='F23'):WealthReturns {
  const scenario=recording.scenarios.find(item=>item.id===scenarioId);
  if(!scenario)throw new Error('没有录制此收益样例');
  if((query.scope==='product')!==!!query.product_id)throw new Error('单产品请选择具体份额代码；整体不能附带产品');
  if(query.period==='cumulative'&&query.anchor_date)throw new Error('累计从可核实起点计算，不接受指定起点');
  const anchor=query.anchor_date??recordedDay;
  if(!/^\d{4}-\d{2}-\d{2}$/.test(anchor)||dateOf(new Date(`${anchor}T00:00:00Z`))!==anchor)throw new Error('请选择有效日期');
  const selected=scenario.products.filter(row=>query.scope==='all'||row.product_id===query.product_id);
  if(!selected.length)throw new Error('所选样例没有该具体份额的收益录制，请切换产品或整体范围');
  const start=query.period==='cumulative'?recording.coverage_start:query.period==='year'?`${anchor.slice(0,4)}-01-01`:query.period==='month'?`${anchor.slice(0,7)}-01`:anchor;
  const naturalEnd=query.period==='year'?`${anchor.slice(0,4)}-12-31`:query.period==='month'?endMonth(anchor):query.period==='cumulative'?recordedDay:anchor;
  const end=start>recordedDay?naturalEnd:naturalEnd<recordedDay?naturalEnd:recordedDay;
  const dailyRanges:{start:string;end:string}[]=[];
  if(query.period==='year'||query.period==='cumulative'){
    for(let date=start;date<=end;){
      const until=query.period==='year'?endMonth(date):`${date.slice(0,4)}-12-31`;
      const stop=until>end?end:until;
      dailyRanges.push({start:date,end:stop});date=nextDay(stop);
    }
  }else for(let date=start;date<=end;date=nextDay(date))dailyRanges.push({start:date,end:date});
  const valuesIn=(row:typeof selected[number],from:string,to:string,field:'pnl'|'fees'):Amount=>{
    if(from<recording.coverage_start||to>recordedDay||to<from)return null;
    return sum(recording.dates.flatMap((day,index)=>day>=from&&day<=to?[row[field][index]]:[]));
  };
  const at=(row:typeof selected[number],field:'realized'|'unrealized'):Amount=>{
    const index=recording.dates.indexOf(end);return index<0?null:row[field][index];
  };
  const products=selected.map(row=>{
    const pnl=valuesIn(row,start,end,'pnl'),cum=valuesIn(row,recording.coverage_start,end,'pnl');
    return {product_id:row.product_id,product_name:row.product_name,period_pnl_fen:pnl,cumulative_pnl_fen:cum,
      status:pnl===null?'partial' as const:'complete' as const,reason:pnl===null?'超出录制覆盖或缺估值／历史成本':null};
  });
  const points:ReturnPoint[]=dailyRanges.map(range=>{
    const value=sum(selected.map(row=>valuesIn(row,range.start,range.end,'pnl')));
    const current=range.end===recordedDay&&query.period!=='cumulative';
    return {period_start:range.start,period_end:range.end,pnl_fen:value,status:value===null?'unavailable':current?'partial':'complete',reason:value===null?'未录制该时段或缺估值／历史成本':current?'本时段尚未结束，仅含录制时点前结果':null};
  });
  const periodPnl=sum(products.map(row=>row.period_pnl_fen));
  const incomplete=naturalEnd>=recordedDay&&start<=recordedDay&&query.period!=='cumulative';
  const status=start>recordedDay||end<recording.coverage_start?'unavailable':periodPnl===null||incomplete?'partial':'complete';
  const missing=[...(periodPnl===null?['selected_period_coverage_or_valuation']:[]),...(incomplete?['period_not_finished']:[])];
  return {schema_version:'wealth-returns-v1',asset_scope_id:`fixture-returns:${scenarioId}`,snapshot_id:`fixture-returns-v1:${scenarioId}:${query.scope}:${query.product_id??'all'}:${query.period}:${anchor}`,
    scope:query.scope,product_id:query.product_id??null,period:query.period,start_date:start,end_date:end,as_of:recording.as_of,timezone:'Asia/Shanghai',coverage_start:recording.coverage_start,status,missing_fields:missing,
    summary:{period_pnl_fen:periodPnl,cumulative_pnl_fen:sum(products.map(row=>row.cumulative_pnl_fen)),realized_pnl_fen:sum(selected.map(row=>at(row,'realized'))),unrealized_pnl_fen:sum(selected.map(row=>at(row,'unrealized'))),fees_fen:sum(selected.map(row=>valuesIn(row,start,end,'fees')))},
    points,products,data_source:recording.data_source,valuation_as_of:end>=recording.coverage_start&&end<=recordedDay?end:null,formula_version:'recorded-contract-expectations-v1'};
}
