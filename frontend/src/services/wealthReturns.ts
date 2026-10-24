/** 00 §6.11读取契约。银行GET /returns接线待B3d账务计算完成。 */
export type ReturnPeriod='day'|'month'|'year'|'cumulative';
export type ReturnStatus='complete'|'partial'|'unavailable';
export interface ReturnsQuery {scope:'all'|'product';product_id?:string;period:ReturnPeriod;anchor_date?:string;overview?:boolean}
export interface WealthPerformance {period:ReturnPeriod;start_date:string;end_date:string;amount_fen:number|null;rate_percent:string|null;notice:string;points:{date:string;rate_percent:string|null}[];positions:{position_key:string;yesterday_pnl_fen:number|null}[];as_of:string;rate_method:string}
export interface ReturnPoint {period_start:string;period_end:string;pnl_fen:number|null;status:ReturnStatus;reason:string|null}
export interface ProductReturn {product_id:string;product_name:string;period_pnl_fen:number|null;cumulative_pnl_fen:number|null;status:ReturnStatus;reason:string|null}
export interface WealthReturns {
  performance?:WealthPerformance|null;
  schema_version:'wealth-returns-v1';asset_scope_id:string;snapshot_id:string;scope:'all'|'product';product_id:string|null;
  period:ReturnPeriod;start_date:string;end_date:string;as_of:string;timezone:'Asia/Shanghai';coverage_start:string|null;
  status:ReturnStatus;missing_fields:string[];
  summary:{period_pnl_fen:number|null;cumulative_pnl_fen:number|null;realized_pnl_fen:number|null;unrealized_pnl_fen:number|null;fees_fen:number|null};
  points:ReturnPoint[];products:ProductReturn[];data_source:string;valuation_as_of:string|null;formula_version:string;
}
export type ReturnsLoader=(query:ReturnsQuery)=>Promise<WealthReturns>;
export interface ReturnScenario {id:string;label:string}

export const returnMoney=(fen:number|null)=>fen===null?'无法完整计算':`${fen>0?'+':''}¥${(fen/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
export const returnStatus:Record<ReturnStatus,string>={complete:'资料完整',partial:'部分资料／时段未完整',unavailable:'暂无可核实数据'};
/** 日历只做日期布局；未知日不补零、不插值。 */
export function calendarCells(points:ReturnPoint[],month:string):(ReturnPoint|null)[]{
  const [year,index]=month.split('-').map(Number);
  const pad=(new Date(Date.UTC(year,index-1,1)).getUTCDay()+6)%7;
  const length=new Date(Date.UTC(year,index,0)).getUTCDate();
  const byDate=new Map(points.map(point=>[point.period_start,point]));
  return [...Array<null>(pad).fill(null),...Array.from({length},(_,day)=>{
    const date=`${month}-${String(day+1).padStart(2,'0')}`;
    return byDate.get(date)??{period_start:date,period_end:date,pnl_fen:null,status:'unavailable' as const,reason:'该日未返回收益事实'};
  })];
}
