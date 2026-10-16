import test from 'node:test';
import assert from 'node:assert/strict';
import recording from '../../tests/fixtures/wealth_portfolio_contract_v1/f09.json' with { type: 'json' };
import { changeShare, displayedShare, emptyProductSelection, groupMessage, performanceText, selectedReferences, selectionForScope, toggleProduct, validShares } from '../src/components/Wealth/productSelection.ts';
import { recordedComparison, recordedDetail, recordedNextGroup } from '../src/components/Wealth/FixtureProductProvider.ts';

const stable = recording.groups.find(group => group.bucket === 'stable')!;
const [both, onlyC, pausedA] = stable.items;
const empty = () => emptyProductSelection('fixture-scope');

test('F09: valid A is the displayed default, selection can proceed without details', () => {
  assert.equal(displayedShare(both, empty())?.share_class, 'A');
  const selected = toggleProduct(empty(), both);
  assert.equal(selectedReferences(selected)[0].product_id, 'fixture-S1-A');
});
test('F09: only-C and paused-A families display/select C with no invalid switch target', () => {
  for (const candidate of [onlyC, pausedA]) {
    assert.equal(displayedShare(candidate, empty())?.share_class, 'C');
    assert.deepEqual(validShares(candidate).map(share => share.share_class), ['C']);
  }
});
test('switching A/C replaces concrete ID and versions in one family seat', () => {
  const updated = structuredClone(both);
  updated.share_classes[1].product_version = 8;
  updated.share_classes[1].disclosure_version = 9;
  const state = changeShare(toggleProduct(empty(), updated), updated, updated.share_classes[1].product_id);
  assert.equal(state.items.length, 1);
  assert.deepEqual(selectedReferences(state)[0], { family_id:both.family_id,product_id:'fixture-S1-C',product_version:8,disclosure_version:9,share_class:'C' });
  assert.equal(changeShare(state, updated, 'fake-product'), state);
  assert.equal(changeShare(state, updated, 'fixture-S1-A').items[0].share.product_id, 'fixture-S1-A');
});
test('selection is unique by family; unchecking retains the preferred share', () => {
  let state = changeShare(toggleProduct(empty(), both), both, 'fixture-S1-C');
  state = toggleProduct(state, onlyC);
  assert.equal(state.items.length, 2);
  state = toggleProduct(state, both);
  assert.equal(state.items.length, 1);
  state = toggleProduct(state, both);
  assert.equal(state.items.length, 2);
  assert.equal(state.items.find(item => item.candidate.family_id === both.family_id)?.share.share_class, 'C');
});
test('rejected/unavailable share cannot be selected or silently switched to', () => {
  const bad = structuredClone(both);
  bad.share_classes.forEach(share => { share.tradable = false; });
  const state = empty();
  assert.equal(displayedShare(bad, state), undefined);
  assert.equal(toggleProduct(state, bad), state);
  assert.equal(changeShare(state, pausedA, pausedA.share_classes[0].product_id), state);
});
test('non-A/C product remains a single product, with no invented share class', () => {
  const cash = recording.groups.find(group => group.bucket === 'cash_management')!.items[0];
  assert.equal(displayedShare(cash, empty())?.share_class, null);
});
test('unknown risk/fee/history remain unavailable rather than zero or over-risk', () => {
  const group = recording.unavailable_group;
  assert.equal(group.items[0].share_classes[0].fees.purchase_fee_rate, null);
  assert.equal(validShares(group.items[0]).length, 0);
  assert.equal(toggleProduct(empty(), group.items[0]).items.length, 0);
  assert.equal(performanceText(group.items[0].share_classes[0].performance.one_year).value, '资料缺失');
  assert.doesNotMatch(groupMessage(group)!, /超过|R[1-5]/);
});
test('changing scope clears all selections and share preferences', () => {
  const state = changeShare(toggleProduct(empty(), both), both, 'fixture-S1-C');
  assert.equal(selectionForScope(state, 'fixture-scope'), state);
  assert.deepEqual(selectionForScope(state, 'new-scope'), emptyProductSelection('new-scope'));
});
test('batch replacement keeps previously selected families and original concrete product', () => {
  const state = toggleProduct(empty(), both);
  const next = recordedNextGroup(stable, state.items.map(item => item.candidate.family_id));
  assert.equal(next.items.length, 3);
  assert.ok(!next.items.some(item => item.family_id === both.family_id));
  assert.equal(state.items[0].share.product_id, 'fixture-S1-A');
  assert.deepEqual(recordedNextGroup(next, [both.family_id]), stable);
});
test('metric labels do not turn annualized yields/benchmarks/NAV changes into total return', () => {
  const p = both.share_classes[0].performance.one_year;
  assert.equal(performanceText({...p,metric:'seven_day_annualized'}).label, '七日年化');
  assert.equal(performanceText({...p,metric:'benchmark'}).label, '业绩比较基准');
  assert.equal(performanceText(p).label, '近一年净值变化');
  assert.equal(performanceText(onlyC.share_classes[0].performance.one_year).value, '样例不足一年');
  assert.equal(performanceText({...p,metric:'unknown'}).value, '数据缺失');
});
test('group status reason is accurate; an ok group never shows a filtered risk warning', () => {
  assert.equal(groupMessage(stable), null);
  assert.match(groupMessage({...stable,status:'no_category_match'})!, /类别/);
  assert.doesNotMatch(groupMessage({...stable,status:'no_eligible_products'})!, /主题|风险/);
  assert.doesNotMatch(groupMessage({...stable,status:'unavailable'})!, /超过/);
  assert.match(groupMessage(recording.risk_group)!, /R2.*R3.*R4/);
});
test('recorded optional details and comparison use selected C IDs and reject unknown data', async () => {
  const detail = await recordedDetail('fixture-S1-C','1y');
  assert.equal(detail.product_id, 'fixture-S1-C');
  assert.match(detail.name,/C类/);
  const rows = await recordedComparison(['fixture-S1-C','fixture-S2-C']);
  assert.equal(rows[0].sales_service_fee_rate,'0.004');
  assert.equal(rows[1].performance_value,'样例不足一年');
  await assert.rejects(recordedComparison(['fixture-S1-C']));
  await assert.rejects(recordedComparison(['fixture-S1-C','fixture-S1-C']));
  await assert.rejects(recordedDetail('bank-account-id','1y'));
});
