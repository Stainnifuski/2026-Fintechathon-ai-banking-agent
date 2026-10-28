import test from 'node:test';
import assert from 'node:assert/strict';
import {activeStep,affectedSteps,agentCommandKey,candidateStage,effectiveStepKind,stepOrder} from '../src/components/Wealth/stepFlow.ts';

test('only the first unfinished registered step is active; absent steps do not block',()=>{
 const order=stepOrder('recommend');
 assert.equal(activeStep(order,['match','candidates','selected','amounts'],[]),'match');
 assert.equal(activeStep(order,['match','candidates','selected','amounts'],['match','selected']),'candidates');
 assert.equal(activeStep(order,['trade'],['match','candidates','selected','amounts','purchase_review']),'trade');
});
test('bucketed recommendation candidates remain visible after match and on task restore',()=>{
 const recommendation=stepOrder('recommend');
 for(const bucket of ['cash_management','stable','growth']){
  const stage=candidateStage(false,bucket);
  assert.equal(stage,'candidates');
  assert.equal(activeStep(recommendation,['match',stage,'selected'],['match']),stage);
  assert.equal(activeStep(stepOrder('planning'),['match',candidateStage(true,bucket)],['match']),bucket);
 }
 assert.equal(candidateStage(false,null),'candidates');
});
test('switching a recommendation card to planning activates product pools and survives restore',()=>{
 const kind=effectiveStepKind('recommend','planning','request-1','request-1');
 assert.equal(kind,'planning');
 assert.equal(activeStep(stepOrder(kind),['match','cash_management','stable','growth','selected'],['match']),'cash_management');
 assert.equal(effectiveStepKind('recommend','planning','request-1','request-2'),'recommend');
 assert.equal(effectiveStepKind('recommend','target','request-1','request-1'),'recommend');
});
test('a new Agent request reapplies a card without treating view saves as new instructions',()=>{
 const first=agentCommandKey('task-1','request-1');
 assert.equal(first,agentCommandKey('task-1','request-1'));
 assert.notEqual(first,agentCommandKey('task-1','request-2'));
 assert.notEqual(first,agentCommandKey('task-2','request-1'));
});
test('planning pool rollback invalidates successors but keeps independent pools',()=>{
 const invalid=affectedSteps('planning','stable');
 assert.ok(invalid.includes('budget')&&invalid.includes('trade'));
 assert.ok(!invalid.includes('cash_management')&&!invalid.includes('growth'));
 assert.deepEqual(affectedSteps('redeem','redemption_input'),['redemption_input','trade']);
});

test('redemption opened from a holdings query activates the inline input card',()=>{
 const order=stepOrder('positions');
 assert.equal(activeStep(order,['match','redemption_input'],[]),'redemption_input');
 assert.equal(activeStep(order,['match','redemption_input','trade'],['redemption_input']),'trade');
 assert.deepEqual(affectedSteps('positions','redemption_input'),['redemption_input','trade']);
});
