import test from 'node:test';
import assert from 'node:assert/strict';
import {recordedTableAssets,recordedTableEligibility,recordedTableDetail} from '../src/components/Wealth/FixtureTableProvider.ts';
import {positionTableData,orderTableData,scaledUnits,pageRows,togglePage,scopeKey,metricText} from '../src/components/Wealth/tableData.ts';

test('F23 aggregates lots by account/product/version with exact four-place shares',()=>{
  const assets=recordedTableAssets();const data=positionTableData(assets,'f23');assert.equal(data.rows.length,9);
  const row=data.rows.find(r=>r.position_key==='fixture-table-A1:fixture-S1-A:1')!;
  assert.equal(row.units,'101.0000');assert.equal(row.redeemable_units,'80.8766');assert.equal(row.lot_ids.length,2);
  assert.equal(row.market_value_fen,10212);assert.equal(row.cost_fen,10089);
  assert.ok(data.rows.some(r=>r.product_id===row.product_id&&r.product_version===2));
  assert.ok(data.rows.some(r=>r.product_id===row.product_id&&r.account_id==='fixture-table-A2'));
});
test('missing valuation stays null and prevents a complete total ratio',()=>{
  const a=recordedTableAssets();a.lots[1].market_value_fen=null;a.lots[1].unrealized_pnl_fen=null;a.valuation_complete=false;
  const rows=positionTableData(a,'f23').rows;assert.ok(rows.every(r=>r.total_ratio===null));
  const row=rows.find(r=>r.position_key==='fixture-table-A1:fixture-S1-A:1')!;assert.equal(row.market_value_fen,null);assert.equal(row.unrealized_pnl_fen,null);
});
test('orders include all states, sorted stably, separate from position references',()=>{
  const a=recordedTableAssets();const o=orderTableData(a,'f23');const p=positionTableData(a,'f23');
  assert.equal(o.rows.length,12);assert.equal(o.rows[0].operation_id,'fixture-table-order-11');
  assert.ok(o.rows.some(r=>r.status==='CANCELLED'));assert.ok(o.rows.every(r=>r.object_type==='orders'));
  assert.ok(p.rows.every(r=>r.object_type==='positions'&&!o.rows.some(o=>o.operation_id===r.position_key)));
});
test('select all only affects current page; selection survives pagination',()=>{
  const rows=orderTableData(recordedTableAssets(),'f23').rows;const p1=pageRows(rows,1,5),p2=pageRows(rows,2,5);
  let selected=togglePage([],p1.rows.map(r=>r.operation_id),true);assert.equal(selected.length,5);
  selected=togglePage(selected,[p2.rows[0].operation_id],true);assert.equal(selected.length,6);
  selected=togglePage(selected,p1.rows.map(r=>r.operation_id),false);assert.deepEqual(selected,[p2.rows[0].operation_id]);
  assert.equal(pageRows(rows,99,5).page,3);assert.throws(()=>pageRows(rows,0,5));
});
test('scope includes authenticated user, full account set and current context',()=>{
  const a=recordedTableAssets();const first=scopeKey(a,'user1');assert.equal(scopeKey({...a,cash:[...a.cash].reverse()},'user1'),first);
  assert.notEqual(scopeKey(a,'user2'),first);assert.notEqual(scopeKey({...a,context_id:'another'},'user1'),first);
  assert.notEqual(scopeKey({...a,cash:a.cash.slice(0,1)},'user1'),first);
});
test('shares retain precision and old records without versions cannot invent a current version',()=>{
  assert.equal(scaledUnits('9007199254740993.1234'),90071992547409931234n);assert.throws(()=>scaledUnits('0.12345'));assert.throws(()=>scaledUnits('-1'));
  const a=recordedTableAssets();delete a.lots[0].version_id;assert.throws(()=>positionTableData(a,'f23'));
});
test('daily quote missing/not applicable never appears as 0%',()=>{
  const a=recordedTableAssets();assert.equal(metricText(a.lots[0].daily_metric),'货币类收益率不是单位净值涨跌');
  assert.equal(metricText(undefined),'缺少行情资料');assert.match(metricText(a.lots[1].daily_metric),/0.5000%/);
});
test('F23 only replays recorded cancellation and matching-version details',async()=>{
  assert.equal((await recordedTableEligibility('fixture-table-order-08')).eligibility,'UNKNOWN');
  await assert.rejects(recordedTableEligibility('bank-real-id'));await assert.rejects(recordedTableDetail('fixture-S1-A','1y',2));
  const detail=await recordedTableDetail('fixture-S1-A','1y',1);assert.equal(detail.product_id,'fixture-S1-A');
  const lot=recordedTableAssets().lots.find(r=>r.product_id==='fixture-S1-A')!;
  assert.equal(detail.latest_nav?.date,lot.nav_date);assert.equal(detail.latest_nav?.nav,lot.nav);
  assert.equal(detail.chart?.points.length,2);
  const points=detail.chart!.points;
  assert.equal(((Number(points[1].nav)/Number(points[0].nav)-1)*100).toFixed(4),lot.daily_metric?.value_percent);
});
