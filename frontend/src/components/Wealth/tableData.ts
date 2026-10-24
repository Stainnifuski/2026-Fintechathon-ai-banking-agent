import type { Assets, DailyMetric, OrderTableData, PositionRow, PositionTableData } from '../../services/wealth';

/** Integer ten-thousandths; summing lots never uses binary floating point. */
export function scaledUnits(value: string): bigint {
  if (!/^\d+(\.\d{1,4})?$/.test(value)) throw new Error('银行份额格式无效');
  const [whole,fraction='']=value.split('.');
  return BigInt(whole)*10000n+BigInt(fraction.padEnd(4,'0'));
}
const unitsText=(value:bigint)=>`${value/10000n}.${String(value%10000n).padStart(4,'0')}`;
export function scopeKey(assets:Assets,userId:string):string {
  return JSON.stringify([userId,assets.asset_summary?.asset_scope_id??assets.context_id??'legacy-v1',assets.asset_summary?.account_ids??assets.cash.map(a=>a.account_id).sort()]);
}
export function orderTableData(assets:Assets,scope:string):OrderTableData {
  return {object_type:'orders',scope_key:scope,rows:assets.orders.map(order=>{
    const at=order.version_id.lastIndexOf(':');
    return {...order,object_type:'orders' as const,product_id:order.product_id??order.version_id.slice(0,at),
      product_version:order.product_version??Number(order.version_id.slice(at+1)),product_name:order.product_name??order.version_id};
  }).sort((a,b)=>(b.accepted_at??'').localeCompare(a.accepted_at??'')||a.operation_id.localeCompare(b.operation_id))};
}
const sumKnown=(values:(number|null)[])=>values.some(v=>v==null)?null:values.reduce<number>((sum,v)=>sum+v!,0);
export function positionTableData(assets:Assets,scope:string):PositionTableData {
  const grouped=new Map<string,Assets['lots']>();
  for(const lot of assets.lots){
    if(!lot.version_id||!lot.product_version)throw new Error('持仓缺少产品版本，请刷新银行事实');
    const key=`${lot.account_id}:${lot.product_id}:${lot.product_version}`;
    grouped.set(key,[...(grouped.get(key)??[]),lot]);
  }
  const total=assets.asset_summary?assets.asset_summary.total_assets_fen:assets.valuation_complete===true?assets.bank_assets_fen:null;
  const basis=total!=null&&Number.isSafeInteger(total)&&total>0?total:null;
  const rows:PositionRow[]=Array.from(grouped.entries()).map(([position_key,lots])=>{
    const first=lots[0];const value=sumKnown(lots.map(l=>l.market_value_fen));
    const sameNav=lots.every(l=>l.nav===first.nav&&l.nav_date===first.nav_date);
    return {object_type:'positions' as const,position_key,account_id:first.account_id,product_id:first.product_id,
      product_version:first.product_version!,product_name:first.product_name,disclosure_version:first.disclosure_version??null,
      share_class:first.share_class??null,lot_ids:lots.map(l=>l.lot_id),lots,
      units:unitsText(lots.reduce((s,l)=>s+scaledUnits(l.units),0n)),
      redeemable_units:unitsText(lots.reduce((s,l)=>s+scaledUnits(l.redeemable_units),0n)),
      held_units:unitsText(lots.reduce((s,l)=>s+scaledUnits(l.held_units),0n)),
      locked_units:unitsText(lots.reduce((s,l)=>s+scaledUnits(l.locked_units),0n)),
      market_value_fen:value,unrealized_pnl_fen:sumKnown(lots.map(l=>l.unrealized_pnl_fen)),cost_fen:lots.reduce((s,l)=>s+l.cost_fen,0),
      total_ratio:basis&&value!=null?String(value/basis):null,nav:sameNav?first.nav??null:null,nav_date:sameNav?first.nav_date:null,
      daily_metric:sameNav?first.daily_metric??null:null};
  }).sort((a,b)=>a.position_key.localeCompare(b.position_key));
  return {object_type:'positions',scope_key:scope,rows,business_date:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(assets.business_at))};
}
export function pageRows<T>(rows:T[],page:number,pageSize:number):{rows:T[];page:number;pages:number} {
  if(!Number.isInteger(page)||page<1||![5,10,20].includes(pageSize))throw new Error('分页参数无效');
  const pages=Math.max(1,Math.ceil(rows.length/pageSize));const current=Math.min(page,pages);
  return {rows:rows.slice((current-1)*pageSize,current*pageSize),page:current,pages};
}
export function togglePage(selected:string[],pageIds:string[],checked:boolean):string[] {
  return checked?Array.from(new Set([...selected,...pageIds])):selected.filter(id=>!pageIds.includes(id));
}
export function metricText(metric:DailyMetric|null|undefined):string {
  if(!metric)return '缺少行情资料';
  return metric.status==='ok'&&metric.value_percent!=null?`${metric.label} ${metric.value_percent}%`:(metric.reason??'暂无适用指标');
}
