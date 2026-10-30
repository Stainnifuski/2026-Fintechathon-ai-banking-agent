import test from 'node:test';
import assert from 'node:assert/strict';
import {wealthFeedback,wealthRequestFailure} from '../src/utils/wealthFeedback.ts';
import {isSessionAuthenticationFailure} from '../src/utils/sessionAuthentication.ts';

test('expired login is an actionable authentication error, not a ratio failure',()=>{
 const error={response:{status:401,data:{detail:'无效的认证令牌'}}};
 assert.equal(isSessionAuthenticationFailure(error),true);
 assert.match(wealthRequestFailure(error),/登录已过期.*重新登录/);
 assert.doesNotMatch(wealthRequestFailure(error),/比例校验未完成|原方案保留/);
});

test('invalid MFA is not mistaken for an expired login session',()=>{
 const error={response:{status:401,data:{detail:{code:'TOTP_INVALID',message:'验证码不正确，请重新输入'}}}};
 assert.equal(isSessionAuthenticationFailure(error),false);
 assert.match(wealthRequestFailure(error),/验证码不正确/);
});

test('request errors preserve actual Chinese boundary and stale-version explanations',()=>{
 assert.match(wealthRequestFailure({response:{status:422,data:{detail:'进取比例须在41%至59%之间'}}}),/41%至59%/);
 assert.match(wealthRequestFailure({response:{status:409,data:{detail:{code:'STALE_PLAN'}}}}),/重新核对当前方案/);
});
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

test('unexpected server failures do not pretend refreshing bank facts repairs server code',()=>{
 for(const data of ['Internal Server Error',{}, {detail:'Internal Server Error'}]){
  const text=wealthRequestFailure({response:{status:500,data}});
  assert.match(text,/系统处理请求时发生异常/);
  assert.doesNotMatch(text,/刷新银行事实/);
  assert.match(text,/核对原操作结果/);
 }
});

test('bank quote failures explain the preview boundary and next step',()=>{
 const message=wealthFeedback('BANK_UNAVAILABLE: database is locked');
 assert.match(message,/结果尚未核实/);
 assert.doesNotMatch(message,/尚未生成交易预览/);
 assert.match(message,/核对差额与准备预览/);
 assert.doesNotMatch(message,/database is locked|BANK_UNAVAILABLE/);
});

test('cash shortfall preserves exact server amounts and an actionable remedy',()=>{
 const message='申购合计22000.00元，借记账户可用20000.00元，还差2000.00元。请减少申购金额或补足资金后重试。';
 assert.equal(wealthRequestFailure({response:{data:{detail:{code:'INSUFFICIENT_AVAILABLE_FUNDS',message}}}}),message);
 assert.match(wealthRequestFailure({response:{data:{detail:{code:'INVALID_AMOUNT',message:'金额不符合产品申购限制'}}}}),/最低及最高金额.*修改/);
 assert.match(wealthFeedback('DATA_STALE'),/稍后重试.*移除/);
});
