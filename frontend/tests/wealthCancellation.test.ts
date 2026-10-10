import test from 'node:test';
import assert from 'node:assert/strict';
import {canCancelOrder} from '../src/services/wealthCancellation.ts';
import type {CancellationRow} from '../src/services/wealthCancellation';
const view=():CancellationRow=>({row:{operation_id:'order-1',account_id:'A1',digest:'digest-1',version_id:'P1:1',disclosure_version:1} as CancellationRow['row'],eligibility:{operation_id:'order-1',order_digest:'digest-1',version_id:'P1:1',eligibility:'ALLOWED',reason:'尚可撤',deadline:null,consequence:'仅释放占用'}});
test('an exact allowed order can enter personal cancellation confirmation',()=>assert.equal(canCancelOrder(view(),[]),true));
test('changed order identity, digest or version cannot reuse previous eligibility',()=>{
  for(const patch of [{operation_id:'other'},{order_digest:'new'},{version_id:'P1:2'}]){
    const value=view();Object.assign(value.eligibility,patch);assert.equal(canCancelOrder(value,[]),false);
  }
});
test('unknown/denied eligibility, missing disclosure and human-controlled account stay blocked',()=>{
  for(const eligibility of ['UNKNOWN','DENIED'] as const){const value=view();value.eligibility.eligibility=eligibility;assert.equal(canCancelOrder(value,[]),false);}
  const value=view();value.row.disclosure_version=undefined;assert.equal(canCancelOrder(value,[]),false);
  assert.equal(canCancelOrder(view(),['A1']),false);assert.equal(canCancelOrder(view(),['A2']),true);
});
