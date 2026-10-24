import type {PositionRow} from '../../services/wealth';
export function holdingsSelection(rows:Pick<PositionRow,'position_key'|'product_id'|'product_version'|'product_name'>[],selected:string[]){
 const refs=(items:typeof rows)=>items.map(r=>({product_id:r.product_id,product_version:r.product_version,name:r.product_name}));
 return {selection_kind:'positions' as const,visible_products:refs(rows).slice(0,60),selected_products:refs(rows.filter(r=>selected.includes(r.position_key)))};
}
