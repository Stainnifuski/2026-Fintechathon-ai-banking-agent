import test from 'node:test';
import assert from 'node:assert/strict';
import {planningCash,availablePlanningCash} from '../src/components/Wealth/planningCash.ts';
test('credit spending limits never enter planning cash',()=>{
  assert.equal(availablePlanningCash([{account_type:'savings',available_fen:12345},{account_type:'credit',available_fen:2000000}]),12345);
});
test('cash and investment sum exactly to current available cents from either field',()=>{
  assert.deepEqual(planningCash(12345,{field:'reserve',text:'23.45'}),{reserve:2345,investment:10000});
  assert.deepEqual(planningCash(12345,{field:'investment',text:'100'}),{reserve:2345,investment:10000});
  assert.deepEqual(planningCash(12345,{field:'investment',text:'0'}),{reserve:12345,investment:0});
  assert.deepEqual(planningCash(0,{field:'reserve',text:'0'}),{reserve:0,investment:0});
});
test('missing, partial, negative, excessive and sub-cent inputs cannot continue',()=>{
  for(const text of ['', '1.', '-1', '123.46', '0.001', '1e2', 'NaN','999999999999999999999'])assert.equal(planningCash(12345,{field:'reserve',text}),null);
  assert.equal(planningCash(100,{field:'reserve',text:'1.01'}),null);
});
test('total investment includes readonly old holdings and only spends new cash',()=>{
  assert.deepEqual(planningCash(12345,{field:'total',text:'600'},50000),{reserve:2345,investment:10000});
  assert.deepEqual(planningCash(12345,{field:'total',text:'500'},50000),{reserve:12345,investment:0});
  assert.equal(planningCash(12345,{field:'total',text:'499.99'},50000),null);
  assert.equal(planningCash(12345,{field:'total',text:'623.46'},50000),null);
});
