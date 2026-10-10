import type {PortfolioPlan,PortfolioSnapshot,ProductCandidate} from './wealth';
import {readCandidateGroup} from './wealthCandidates.ts';
import {emptyProductSelection,toggleProduct,changeShare} from '../components/Wealth/productSelection.ts';
type Obj=Record<string,unknown>;
const obj=(v:unknown):v is Obj=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown)=>typeof v==='string'&&v.length>0;
const integer=(v:unknown)=>Number.isSafeInteger(v)&&Number(v)>=0;
const ratio=(v:unknown)=>typeof v==='string'&&/^\d+(\.\d+)?$/.test(v)&&Number(v)<=1;
const names=['cash_management','stable','growth'];
const strings=(v:unknown)=>Array.isArray(v)&&v.every(x=>typeof x==='string');
const error=()=>{throw new Error('整体方案资料不完整或范围不匹配，请重新读取；不能准备交易。');};
/** Check the existing shared DTO fields needed by the consumer; no A model. */
export function readPortfolioPlan(value:unknown,context:string):PortfolioPlan{
  if(!obj(value)||value.schema_version!=='wealth-portfolio-plan-v1'||value.context_id!==context||!text(value.portfolio_id)||!integer(value.version)||Number(value.version)<1
    ||!text(value.snapshot_id)||!text(value.asset_scope_id)||!text(value.as_of)||!text(value.provider_id)||!text(value.provider_version)||!text(value.model_status)||!text(value.basis_kind)||!text(value.basis_status)
    ||!(value.basis_fen===null||integer(value.basis_fen))||!integer(value.cash_residual_fen)||!Array.isArray(value.buckets)||value.buckets.length!==3
    ||!Array.isArray(value.candidate_groups)||!Array.isArray(value.selected_positions)||!strings(value.missing_fields)||!strings(value.reasons)||!obj(value.data_versions))return error();
  const seen=new Set();
  for(const b of value.buckets){if(!obj(b)||!names.includes(String(b.bucket))||seen.has(b.bucket)||!ratio(b.min_ratio)||!ratio(b.target_ratio)||!ratio(b.max_ratio)
    ||Number(b.min_ratio)>Number(b.target_ratio)||Number(b.target_ratio)>Number(b.max_ratio)||!(b.target_fen===null||integer(b.target_fen))||!(b.current_fen===null||integer(b.current_fen)))return error();seen.add(b.bucket);}
  const families=new Set();
  for(const p of value.selected_positions){if(!obj(p)||!text(p.account_id)||!text(p.family_id)||families.has(p.family_id)||!text(p.product_id)||!integer(p.product_version)||Number(p.product_version)<1||!integer(p.disclosure_version)||Number(p.disclosure_version)<1
    ||![null,'A','C'].includes(p.share_class as null|string)||!names.includes(String(p.bucket))||!ratio(p.target_ratio_total)||!integer(p.target_fen)||!(p.current_fen===null||integer(p.current_fen)))return error();families.add(p.family_id);}
  return {...value,candidate_groups:value.candidate_groups.map(readCandidateGroup)} as unknown as PortfolioPlan;
}
export function readReviewSnapshot(value:unknown,plan:PortfolioPlan):PortfolioSnapshot{
  if(!obj(value)||value.schema_version!=='wealth-asset-snapshot-v1'||value.context_id!==plan.context_id||value.snapshot_id!==plan.snapshot_id||value.asset_scope_id!==plan.asset_scope_id||value.basis_fen!==plan.basis_fen
    ||typeof value.valuation_complete!=='boolean'||!Array.isArray(value.positions)||!Array.isArray(value.accounts)||!strings(value.account_ids)||!integer(value.receivable_fen))return error();
  for(const p of value.positions)if(!obj(p)||!text(p.position_key)||!text(p.account_id)||!text(p.product_id)||!integer(p.product_version)||!text(p.units)||!text(p.redeemable_units)||!(p.market_value_fen===null||integer(p.market_value_fen)))return error();
  const accounts=value.account_ids as string[];
  if(value.positions.some(p=>!accounts.includes(p.account_id))||plan.selected_positions.some(p=>!accounts.includes(p.account_id)))return error();
  return value as unknown as PortfolioSnapshot;
}
export function planProductSelection(plan:PortfolioPlan){
  let state=emptyProductSelection(plan.context_id);
  const candidates:ProductCandidate[]=[];
  for(const group of plan.candidate_groups)if(group.status==='ok')candidates.push(...group.items);
  for(const p of plan.selected_positions){
    const c=candidates.find(c=>c.family_id===p.family_id);
    if(c&&c.share_classes.some(s=>s.product_id===p.product_id&&s.product_version===p.product_version&&s.disclosure_version===p.disclosure_version)){
      state=changeShare(state,c,p.product_id);state=toggleProduct(state,c);
    }
  }
  return state;
}
