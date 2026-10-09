import test from 'node:test';
import assert from 'node:assert/strict';
import {recordedReturns} from '../src/components/Wealth/FixtureReturnsProvider.ts';
import {calendarCells,returnMoney} from '../src/services/wealthReturns.ts';

test('F20本金不产生利润；F21定价盈利、到账不再次产生利润',()=>{
  const principal=recordedReturns({scope:'all',period:'day',anchor_date:'2026-10-08'},'F20');
  assert.equal(principal.summary.period_pnl_fen,0);
  assert.equal(principal.summary.cumulative_pnl_fen,0);
  const price=recordedReturns({scope:'all',period:'day',anchor_date:'2026-10-07'},'F21');
  const arrival=recordedReturns({scope:'all',period:'day',anchor_date:'2026-10-08'},'F21');
  assert.equal(price.summary.period_pnl_fen,10000);
  assert.equal(arrival.summary.period_pnl_fen,0);
  assert.equal(arrival.summary.cumulative_pnl_fen,10000);
  assert.equal(arrival.summary.realized_pnl_fen,10000);
});
test('F22录制净收益不再次扣除费用明细',()=>{
  const result=recordedReturns({scope:'all',period:'cumulative'},'F22');
  assert.equal(result.summary.period_pnl_fen,800);
  assert.equal(result.summary.fees_fen,300);
  assert.equal(result.summary.unrealized_pnl_fen,800);
});
test('具体份额贡献含已赎回产品，整体不只累加持仓第一页',()=>{
  const all=recordedReturns({scope:'all',period:'cumulative'});
  assert.equal(all.products.length,8);
  assert.equal(all.summary.cumulative_pnl_fen,7*123+250);
  for(const row of all.products){
    const product=recordedReturns({scope:'product',product_id:row.product_id,period:'cumulative'});
    assert.deepEqual(product.products,[row]);
    assert.equal(product.summary.cumulative_pnl_fen,row.cumulative_pnl_fen);
  }
  assert.throws(()=>recordedReturns({scope:'product',product_id:'fixture-S1-C',period:'day'}),/没有该具体份额/);
});
test('月与年覆盖外是null，累计仅从录制覆盖起点；未来与历史缺失不造零',()=>{
  const month=recordedReturns({scope:'all',period:'month',anchor_date:'2026-10-08'});
  assert.equal(month.points.length,9);assert.equal(month.points[0].pnl_fen,null);
  assert.equal(month.summary.period_pnl_fen,null);assert.equal(month.status,'partial');
  const year=recordedReturns({scope:'all',period:'year',anchor_date:'2026-10-08'});
  assert.equal(year.points.length,10);assert.equal(year.status,'partial');
  const cumulative=recordedReturns({scope:'all',period:'cumulative'});
  assert.equal(cumulative.start_date,'2026-10-07');assert.equal(cumulative.points.length,1);
  for(const date of ['2026-09-01','2026-11-01']){
    const absent=recordedReturns({scope:'all',period:'day',anchor_date:date});
    assert.equal(absent.status,'unavailable');assert.equal(absent.summary.period_pnl_fen,null);
    assert.equal(absent.summary.cumulative_pnl_fen,null);
  }
  assert.throws(()=>recordedReturns({scope:'all',period:'cumulative',anchor_date:'2020-01-01'}),/不接受指定起点/);
});
test('F24未知收益传递为null，已知产品可独立查看；当前未结束日标部分',()=>{
  const all=recordedReturns({scope:'all',period:'cumulative'},'F24');
  assert.equal(all.status,'partial');assert.equal(all.summary.period_pnl_fen,null);
  assert.equal(all.summary.cumulative_pnl_fen,null);
  const known=recordedReturns({scope:'product',product_id:'fixture-G1-A',period:'cumulative'},'F24');
  assert.equal(known.summary.cumulative_pnl_fen,123);assert.equal(known.status,'complete');
  const today=recordedReturns({scope:'all',period:'day',anchor_date:'2026-10-09'});
  assert.equal(today.status,'partial');assert.equal(today.points[0].status,'partial');
});
test('日历按北京时间日期字符串排布，可区分零收益与无数据',()=>{
  const result=recordedReturns({scope:'all',period:'month',anchor_date:'2026-10-08'},'F21');
  const cells=calendarCells(result.points,'2026-10');
  assert.equal(cells.length,34);assert.deepEqual(cells.slice(0,3),[null,null,null]);
  assert.equal(cells[3]?.period_start,'2026-10-01');assert.equal(cells[3]?.pnl_fen,null);
  assert.equal(cells[10]?.period_start,'2026-10-08');assert.equal(cells[10]?.pnl_fen,0);
  assert.equal(cells[33]?.pnl_fen,null);assert.equal(returnMoney(null),'无法完整计算');assert.equal(returnMoney(0),'¥0.00');
});
