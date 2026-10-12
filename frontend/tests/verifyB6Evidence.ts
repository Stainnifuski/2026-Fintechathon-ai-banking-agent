/** Explicit consumer check over a passing isolated HTTP test transcript. */
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {readPortfolioPlan,readReviewSnapshot,planProductSelection} from '../src/services/wealthPortfolioReview.ts';
import {readCandidateGroup} from '../src/services/wealthCandidates.ts';
import {initialTableState,currentTableState,selectTablePage} from '../src/components/Wealth/tableState.ts';
import {pageRows} from '../src/components/Wealth/tableData.ts';

const evidence=JSON.parse(readFileSync(process.argv[2],'utf8'));
assert.equal(evidence.source,'isolated_bank_http');
const plan=readPortfolioPlan(evidence.plan,evidence.context_id);
readReviewSnapshot(evidence.snapshot,plan);
const selected=planProductSelection(plan);
assert.equal(selected.items.length,7);
assert.equal(new Set(selected.items.map(p=>p.candidate.family_id)).size,7);
for(const group of evidence.candidate_responses)readCandidateGroup(group);
assert.equal(evidence.orders.object_type,'orders');assert.equal(evidence.positions.object_type,'positions');
assert.equal(evidence.orders.rows.length,9);assert.equal(evidence.positions.rows.length,6);
let orders=selectTablePage(initialTableState(evidence.orders.scope_key),evidence.orders.rows,(row:any)=>row.operation_id,true);
orders={...orders,page:2};orders=selectTablePage(orders,evidence.orders.rows,(row:any)=>row.operation_id,true);
assert.equal(orders.selected.length,9);
const positions=selectTablePage(initialTableState(evidence.positions.scope_key),evidence.positions.rows,(row:any)=>row.position_key,true);
assert.equal(positions.selected.length,5);assert.equal(pageRows(evidence.positions.rows,2,5).rows.length,1);
assert.equal(currentTableState(orders,'changed',evidence.orders.rows,(row:any)=>row.operation_id).selected.length,0);
assert.equal(evidence.returns.length,8);
assert.deepEqual(new Set(evidence.returns.map((r:any)=>r.period)),new Set(['day','month','year','cumulative']));
for(const row of evidence.returns){
  assert.equal(row.asset_scope_id,evidence.before.asset_scope_id);
  assert.equal(row.formula_version,'actual-settlement-replay-v1');
}
assert.equal(evidence.held.total_assets_fen,evidence.before.total_assets_fen);
assert.equal(evidence.held.available_cash_fen,0);
assert.equal(evidence.released.available_cash_fen,300000);
assert.equal(evidence.settled.held_fen,0);
assert.equal(evidence.final_summary.redemption_receivable_fen,0);
console.log('B6 API transcript consumed: seven products, nine orders, six positions, independent paging/selection, eight returns queries and two redemption inputs.');
