import type {NovaWealthCommand,NovaWealthView} from '../../services/nova';

/** Restore the user's selection, binding each amount to its own product identity. */
export function restoreWealthInputs(command:NovaWealthCommand,view?:NovaWealthView){
 const products=view?.selected_products??command.products??[];
 const amounts_yuan=products.map(product=>{
  const index=command.products.findIndex(p=>p.product_id===product.product_id&&p.product_version===product.product_version);
  return view?.amounts_yuan?.[product.product_id]??(index>=0?command.amounts_yuan?.[index]??null:null);
 });
 return {products,amounts_yuan};
}
