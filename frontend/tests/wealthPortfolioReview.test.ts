import test from 'node:test';
import assert from 'node:assert/strict';
import fixture from '../../tests/fixtures/wealth_portfolio_contract_v1/f11.json' with {type:'json'};
import candidates from '../../tests/fixtures/wealth_portfolio_contract_v1/f09.json' with {type:'json'};
import {readPortfolioPlan,readReviewSnapshot,planProductSelection} from '../src/services/wealthPortfolioReview.ts';

test('F11 plan and revision bind to the same complete snapshot',()=>{
  for(const raw of [fixture.plan,fixture.revision]){
    const plan=readPortfolioPlan(raw,'fixture-f11');
    assert.equal(readReviewSnapshot(fixture.snapshot,plan).snapshot_id,plan.snapshot_id);
  }
});
test('foreign context, malformed weights and duplicate families cannot enter review',()=>{
  assert.throws(()=>readPortfolioPlan(fixture.plan,'other'));
  assert.throws(()=>readPortfolioPlan({...fixture.plan,buckets:[{...fixture.plan.buckets[0],target_ratio:'2'},...fixture.plan.buckets.slice(1)]},'fixture-f11'));
  assert.throws(()=>readPortfolioPlan({...fixture.plan,selected_positions:[fixture.plan.selected_positions[0],fixture.plan.selected_positions[0]]},'fixture-f11'));
});
test('snapshot cannot be substituted by current cash or another account scope',()=>{
  const plan=readPortfolioPlan(fixture.plan,'fixture-f11');
  for(const snapshot of [{...fixture.snapshot,snapshot_id:'new'}, {...fixture.snapshot,basis_fen:999}, {...fixture.snapshot,account_ids:['foreign']}])assert.throws(()=>readReviewSnapshot(snapshot,plan));
});
test('server selected targets restore exact shares without inventing weights or missing candidates',()=>{
  const plan=readPortfolioPlan({...fixture.plan,candidate_groups:candidates.groups},'fixture-f11');
  const state=planProductSelection(plan);
  assert.equal(state.items.length,7);
  for(const item of state.items)assert.ok(plan.selected_positions.some(p=>p.product_id===item.share.product_id&&p.product_version===item.share.product_version));
  assert.equal(planProductSelection(readPortfolioPlan(fixture.plan,'fixture-f11')).items.length,0);
});
