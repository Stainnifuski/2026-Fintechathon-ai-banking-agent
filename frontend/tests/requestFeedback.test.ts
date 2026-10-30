import test from 'node:test';
import assert from 'node:assert/strict';
import {requestFeedback} from '../src/utils/requestFeedback.ts';
import {tradeStatusLabel} from '../src/components/Wealth/tradeStatus.ts';

test('network and server failures never claim an operation was not executed',()=>{
 for(const error of [new Error('Network Error'),{response:{status:500,data:{detail:{message:'服务器错误'}}}}]){
  const text=requestFeedback(error);
  assert.match(text,/结果尚未核实.*核对原任务/);
  assert.doesNotMatch(text,/尚未执行|未下单|请重新发送/);
 }
});
test('MFA denial stays distinct from expired login and does not expose codes',()=>{
 const text=requestFeedback({response:{status:401,data:{detail:{code:'TOTP_INVALID'}}}});
 assert.match(text,/验证码.*当前请求未获通过/);assert.doesNotMatch(text,/登录已过期|TOTP_INVALID/);
 assert.match(requestFeedback({response:{status:403,data:{detail:{message:'不能操作他人账户'}}}}),/不能操作他人账户/);
 assert.equal(requestFeedback(new Error('请先选择一个产品')),'请先选择一个产品');
});
test('preparation, confirmation, validation and settlement have distinct factual labels',()=>{
 assert.match(tradeStatusLabel('AWAITING_CONFIRMATION'),/待本人确认/);
 assert.match(tradeStatusLabel('AWAITING_MFA'),/待验证码验证/);
 assert.match(tradeStatusLabel('VALID'),/待提交/);
 assert.equal(tradeStatusLabel('SETTLED'),'已成交');
 assert.match(tradeStatusLabel('NEW_UNKNOWN_STATUS'),/待核对/);
});
