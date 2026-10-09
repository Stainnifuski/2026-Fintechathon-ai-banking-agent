import {post} from './wealth';
export type ActionName='plan_portfolio'|'recommend_products'|'revise_portfolio'|'show_product_detail'|'compare_products'|'list_orders'|'list_positions'|'show_wealth_returns'|'prepare_purchase'|'preview_redemption'|'preview_order_cancellation';
export interface ActionResult {schema_version:'wealth-action-result-v1';request_id:string;context_id:string;session_id:string;session_version:number;action:ActionName;status:'ok'|'clarify'|'blocked';message:string;data:unknown;required_fields:{field:string;reason:string;options?:{id:string;label:string}[]|null}[];allowed_actions:ActionName[]}
export interface ActionRequest {schema_version:'wealth-action-v1';request_id:string;context_id:string;session_id?:string;expected_session_version?:number;selection_context?:{object_type:'products'|'orders'|'positions';ids:string[]};action:ActionName;params:Record<string,unknown>}
export const sendAction=(request:ActionRequest)=>post<ActionResult>('/actions',request);
