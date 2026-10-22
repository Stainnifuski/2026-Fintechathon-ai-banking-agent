import test from 'node:test';
import assert from 'node:assert/strict';
import {incrementalBudget} from '../src/components/Wealth/incrementalBudget.ts';

test('soft overweight old holdings stay; candidate pools only cover actual new purchases',()=>{
  assert.deepEqual(incrementalBudget(4,1000000,[0,300000,500000],0,190000,10000,[200000,380000,420000]),{targets:[200000,300000,500000],sales:[0,0,0],purchases:[190000,0,0]});
});
test('hard overweight and conservative fee allowance match server integer arithmetic',()=>{
  assert.deepEqual(incrementalBudget(3,1000000,[100000,0,800000],0,100000,0,[200000,600000,200000]),{targets:[100000,450000,450000],sales:[0,0,350000],purchases:[0,450000,0]});
  const r=incrementalBudget(3,1000000,[100000,0,800000],0,100000,0,[200000,600000,200000],'.015');
  // API supplies normalized Decimal strings (0.015).
  assert.equal(r,null);
  const normalized=incrementalBudget(3,1000000,[100000,0,800000],0,100000,0,[200000,600000,200000],'0.015')!;
  assert.equal(normalized.sales[2],352645);
});
test('all risk levels and full cash reservation preserve integer budgets',()=>{
  for(let level=1;level<=5;level++)for(const reserve of [0,50000,200000]){
    const r=incrementalBudget(level,1000000,[100000,300000,400000],reserve,200000,0,[200000,400000,400000])!;
    assert.ok(r.purchases.every(n=>n>=0));assert.equal(r.purchases.reduce((a,b)=>a+b),200000-reserve+r.sales.reduce((a,b)=>a+b));
  }
});
