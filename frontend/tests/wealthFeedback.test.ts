import test from 'node:test';
import assert from 'node:assert/strict';
import {wealthFeedback} from '../src/utils/wealthFeedback.ts';
test('missing assessment explains the required questionnaire and personal confirmation',()=>{
 const message=wealthFeedback('FORMAL_ASSESSMENT_UNAVAILABLE：ASSESSMENT_REQUIRED');
 assert.match(message,/回答9题并提交/);assert.match(message,/本人确认/);assert.doesNotMatch(message,/ASSESSMENT/);
});
test('unconfirmed and prohibited assessments give distinct actions without bypass',()=>{
 assert.match(wealthFeedback('FORMAL_ASSESSMENT_UNAVAILABLE:ASSESSMENT_UNCONFIRMED'),/已填写/);
 assert.match(wealthFeedback('ASSESSMENT_PURCHASE_FORBIDDEN'),/不能跳过/);
});
test('unproven binding does not ask the user to repeatedly fill questionnaire',()=>{
 assert.match(wealthFeedback('FORMAL_ASSESSMENT_UNAVAILABLE:ASSESSMENT_BINDING_UNPROVEN'),/不要反复重填/);
});
test('ordinary Chinese success stays unchanged and unknown codes are not displayed',()=>{
 assert.equal(wealthFeedback('申购已受理'),'申购已受理');
 assert.doesNotMatch(wealthFeedback('UNRECOGNIZED_SERVER_CODE'),/SERVER_CODE/);
});
