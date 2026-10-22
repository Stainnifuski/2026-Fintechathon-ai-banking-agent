import test from 'node:test';
import assert from 'node:assert/strict';
import {activeStep,affectedSteps,stepOrder} from '../src/components/Wealth/stepFlow.ts';

test('only the first unfinished registered step is active; absent steps do not block',()=>{
 const order=stepOrder('recommend');
 assert.equal(activeStep(order,['match','candidates','selected','amounts'],[]),'match');
 assert.equal(activeStep(order,['match','candidates','selected','amounts'],['match','selected']),'candidates');
 assert.equal(activeStep(order,['trade'],['match','candidates','selected','amounts','purchase_review']),'trade');
});
test('planning pool rollback invalidates successors but keeps independent pools',()=>{
 const invalid=affectedSteps('planning','stable');
 assert.ok(invalid.includes('budget')&&invalid.includes('trade'));
 assert.ok(!invalid.includes('cash_management')&&!invalid.includes('growth'));
 assert.deepEqual(affectedSteps('redeem','redemption_input'),['redemption_input','trade']);
});
