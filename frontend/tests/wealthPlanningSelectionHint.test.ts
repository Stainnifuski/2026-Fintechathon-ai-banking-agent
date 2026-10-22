import test from 'node:test';
import assert from 'node:assert/strict';
import type {PortfolioPlan} from '../src/types/wealthPortfolio';
import {planningSelectionHint} from '../src/components/Wealth/planningSelectionHint.ts';
function plan(holdings:[string,number][]=[]){
  return {basis_fen:100000,buckets:[{bucket:'cash_management',target_fen:20000},{bucket:'stable',target_fen:40000},{bucket:'growth',target_fen:40000}],
    model_view:{snapshot:{valuation_complete:true,accounts:[{balance_fen:100000-holdings.reduce((sum,[,v])=>sum+v,0),available_fen:100000-holdings.reduce((sum,[,v])=>sum+v,0)}],receivable_fen:0,
      positions:holdings.map(([bucket,market_value_fen])=>({bucket,market_value_fen}))}}} as unknown as PortfolioPlan;
}
test('candidate header names every needed pool immediately; red feedback names only missing pools',()=>{
  const hint=planningSelectionHint(plan(),null,['cash_management','stable']);
  assert.deepEqual(hint.required,['cash_management','stable','growth']);
  assert.match(hint.description,/活钱候选池、稳健候选池、进取候选池/);
  assert.equal(hint.warning,'请在进取候选池中至少选择一只产品。');
  assert.equal(planningSelectionHint(plan(),0,['cash_management','stable','growth']).warning,'');
});
test('cash budget removes the liquid pool requirement and preserves stable/growth requirements',()=>{
  const hint=planningSelectionHint(plan(),20000,['stable']);
  assert.deepEqual(hint.required,['stable','growth']);
  assert.equal(hint.warning,'请在进取候选池中至少选择一只产品。');
  assert.deepEqual(planningSelectionHint(plan(),100000,[]).required,[]);
});
test('existing holdings cover their own bucket, not unrelated pools',()=>{
  const hint=planningSelectionHint(plan([['growth',40000],['stable',10000]]),20000,[]);
  assert.deepEqual(hint.required,['stable']);
  assert.equal(hint.warning,'请在稳健候选池中至少选择一只产品。');
  assert.deepEqual(planningSelectionHint(plan([['growth',40000],['stable',40000]]),20000,[]).required,[]);
});
test('unknown positions do not produce a false no-selection-needed message',()=>{
  const p=plan();p.model_view!.snapshot.valuation_complete=false;
  assert.match(planningSelectionHint(p,0,[]).description,/尚未核实/);
  assert.match(planningSelectionHint(null,0,[]).description,/先生成理财规划/);
});
