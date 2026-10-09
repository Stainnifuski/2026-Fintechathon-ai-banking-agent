import type {AssetSummary} from '../../services/wealth';
export function summaryMoney(fen:number|null){return fen===null?'缺少估值':`¥${(fen/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;}
export function summaryTotal(data:AssetSummary|null,error=''){
  if(error)return '暂无法查询';
  if(!data)return '正在读取…';
  return data.valuation_complete&&data.total_assets_fen!==null?summaryMoney(data.total_assets_fen):'暂无法完整估值';
}
