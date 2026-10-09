import test from 'node:test';
import assert from 'node:assert/strict';
import { percentageRatio, collectPortfolioSelection, selectionKey } from '../src/components/Wealth/allocationSelection.ts';
import { recordedF11Plan, recordedF11Selection, recordedRevision, recordedPrepare } from '../src/components/Wealth/FixtureProductProvider.ts';

test('percentages are exact ratio strings; invalid/over-precise input is rejected',()=>{
  assert.equal(percentageRatio('15.25'),'0.1525');
  assert.equal(percentageRatio('100'),'1.0000');
  for(const text of ['-1','101','15.001','NaN','1e2',''])assert.throws(()=>percentageRatio(text));
});
const input=()=>{const plan=recordedF11Plan();return collectPortfolioSelection(plan,recordedF11Selection(),Object.fromEntries(plan.selected_positions.map(p=>[p.product_id,String(Number(p.target_ratio_total)*100)])));};
test('seven concrete product references and total ratios reach the revision callback',()=>{
  const selection=input();assert.equal(selection.items.length,7);assert.equal(selection.expected_version,2);
  assert.equal(selection.items.find(i=>i.product_id==='fixture-S2-C')?.share_class,'C');
  assert.equal(selection.items.find(i=>i.product_id==='fixture-S3-C')?.target_ratio_total,'0.1000');
  const changed=recordedF11Selection();changed.scopeKey='other';assert.throws(()=>collectPortfolioSelection(recordedF11Plan(),changed,{}));
});
test('recorded revisions replay only registered targets, not a replacement A algorithm',async()=>{
  const selection=input();assert.equal((await recordedRevision(selection)).version,2);
  selection.items.find(i=>i.product_id==='fixture-S1-A')!.target_ratio_total='0.15';
  selection.items.find(i=>i.product_id==='fixture-S2-C')!.target_ratio_total='0.05';
  const plan=await recordedRevision(selection);assert.equal(plan.version,3);
  assert.equal(plan.selected_positions[1].target_fen,150000);
  selection.items[1].target_ratio_total='0.14';await assert.rejects(recordedRevision(selection));
});
test('prepare sample keeps all seven actions and cannot issue bank execution IDs',async()=>{
  const result=await recordedPrepare(recordedF11Plan());assert.equal(result.actions.length,7);
  assert.equal(result.actions.reduce((sum,a)=>sum+a.amount_fen,0),1000000);
  assert.equal(result.execution_plan_id,null);assert.equal(result.execution_plan_version,null);
  const modified=recordedF11Plan();modified.selected_positions[0].target_fen=1;
  await assert.rejects(recordedPrepare(modified));
});
test('validation identity uses actual references and ratios, independent of row order',()=>{
  const selection=input();const reversed={...selection,items:[...selection.items].reverse()};
  assert.equal(selectionKey(selection),selectionKey(reversed));
  reversed.items[0]={...reversed.items[0],product_version:2};assert.notEqual(selectionKey(selection),selectionKey(reversed));
});
