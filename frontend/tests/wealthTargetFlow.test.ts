import test from 'node:test';
import assert from 'node:assert/strict';
import {restoreWealthInputs} from '../src/components/Wealth/restoreInputs.ts';
import {targetAmountState} from '../src/components/Wealth/targetAmounts.ts';
import {activeStep,reconcileCompletedSteps,stepOrder} from '../src/components/Wealth/stepFlow.ts';
import {showNovaMessage} from '../src/components/novaMessageVisibility.ts';

test('restored search selections retain amounts even without command product references',()=>{
 const product={product_id:'P1',product_version:1,name:'测试产品'};
 const command={products:[],amounts_yuan:[]} as any;
 const restored=restoreWealthInputs(command,{selected_products:[product],visible_products:[],amounts_yuan:{P1:'100'}});
 assert.deepEqual(restored,{products:[product],amounts_yuan:['100']});
 assert.equal(targetAmountState(['P1'],{P1:restored.amounts_yuan[0]!}).totalFen,10000);
 const changed=restoreWealthInputs({products:[product],amounts_yuan:['100']} as any,
  {selected_products:[{...product,product_id:'P2'}],visible_products:[]});
 assert.deepEqual(changed.amounts_yuan,[null]);
});

test('refresh after purchase or redemption preparation cannot hide the verification step',()=>{
 for(const kind of ['recommend','planning','redeem']){
  const done=stepOrder(kind).filter(stage=>stage!=='trade');
  const reconciled=reconcileCompletedSteps(done,[],false);
  assert.equal(activeStep(stepOrder(kind),[...done,'trade'],reconciled),'trade');
  assert.deepEqual(reconcileCompletedSteps(done,[],true),[]);
 }
 assert.deepEqual(reconcileCompletedSteps([],['amounts'],false),[],'stale saves cannot undo local invalidation');
});

test('purchase progression requires valid positive amounts for every selected product',()=>{
 for(const value of ['', '0','-1','1.001','NaN','20000000.01'])
  assert.equal(targetAmountState(['P1'],{P1:value}).valid,false,value);
 assert.equal(targetAmountState(['P1','P2'],{P1:'100'}).valid,false);
 assert.deepEqual(targetAmountState(['P1','P2'],{P1:'100.01',P2:'0.01'}),{valid:true,totalFen:10002});
 assert.equal(targetAmountState([],{}).valid,false);
});

test('all business cards hide blank and legacy local-update bubbles while preserving actual replies',()=>{
 assert.equal(showNovaMessage({role:'assistant',content:''}),false);
 assert.equal(showNovaMessage({role:'assistant',content:'已更新操作，请核对下方卡片。',request_id:'action-old'}),false);
 assert.equal(showNovaMessage({role:'assistant',content:'已更新操作，请核对下方卡片。',request_id:'chat-real'}),true);
 assert.equal(showNovaMessage({role:'assistant',content:'',streaming:true}),true);
 assert.equal(showNovaMessage({role:'user',content:'已更新操作，请核对下方卡片。'}),true);
});
