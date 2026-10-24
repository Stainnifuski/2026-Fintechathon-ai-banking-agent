import test from 'node:test';
import assert from 'node:assert/strict';
import {holdingsSelection} from '../src/components/Wealth/holdingsSelection.ts';
test('holdings context preserves only explicit selections across local pages and clear',()=>{
 const rows=Array.from({length:8},(_,i)=>({position_key:`K${i}`,product_id:`P${i}`,product_version:1,product_name:`产品${i}`}));
 const view=holdingsSelection(rows,['K1','K6','unknown']);
 assert.equal(view.selection_kind,'positions');assert.equal(view.visible_products.length,8);
 assert.deepEqual(view.selected_products.map(p=>p.product_id),['P1','P6']);
 assert.deepEqual(holdingsSelection(rows,[]).selected_products,[]);
});
