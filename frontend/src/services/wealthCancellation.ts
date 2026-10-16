import type {OrderRow,CancellationEligibility} from './wealth';
export interface CancellationRow {row:OrderRow;eligibility:CancellationEligibility}
export function canCancelOrder({row,eligibility}:CancellationRow,blockedAccounts:string[]){
  return eligibility.eligibility==='ALLOWED'&&eligibility.operation_id===row.operation_id&&eligibility.order_digest===row.digest&&eligibility.version_id===row.version_id&&row.disclosure_version!=null&&!blockedAccounts.includes(row.account_id);
}
