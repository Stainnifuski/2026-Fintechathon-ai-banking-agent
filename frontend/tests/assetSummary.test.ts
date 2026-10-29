import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {AssetSummaryStore} from '../src/services/assetSummaryStore.ts';
import {changesAssetFacts,invalidateAssetSummary,subscribeAssetSummaryChanges} from '../src/services/assetSummaryEvents.ts';
import {summaryMoney,summaryTotal} from '../src/components/AssetSummary/view.ts';
import type {AssetSummary} from '../src/services/wealth.ts';
const cases=JSON.parse(readFileSync(new URL('../../tests/fixtures/wealth_portfolio_contract_v1/asset_summary_cases.json',import.meta.url),'utf8')) as Record<string,AssetSummary>;
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(reason:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

test('the shared cache coalesces page reads and retains the same canonical DTO',async()=>{
  const request=deferred<AssetSummary>();let calls=0;
  const store=new AssetSummaryStore(()=>{calls++;return request.promise;});
  const home=store.refresh('U1');const wealth=store.refresh('U1');
  assert.equal(home,wealth);assert.equal(calls,1);
  request.resolve(cases.F18);await home;
  assert.equal(store.getSnapshot().data,cases.F18);
  assert.equal(store.getSnapshot().data!.total_assets_fen,1_000_000);
  assert.equal(store.getSnapshot().data!.held_fen,100_000);
});
test('a late pre-switch response cannot restore the previous asset scope',async()=>{
  const old=deferred<AssetSummary>(),fresh=deferred<AssetSummary>();let calls=0;
  const store=new AssetSummaryStore(()=>++calls===1?old.promise:fresh.promise);
  const first=store.refresh('U1');store.invalidate();
  assert.equal(store.getSnapshot().data,null);
  const second=store.refresh('U1');fresh.resolve(cases.F10);await second;
  old.resolve(cases.F18);await first;
  assert.equal(store.getSnapshot().data,cases.F10);
});
test('logout and another owner clear data and reject the old owner response',async()=>{
  const old=deferred<AssetSummary>();let calls=0;
  const store=new AssetSummaryStore(()=>++calls===1?old.promise:Promise.resolve(cases.F01));
  const first=store.refresh('U1');store.setOwner(null);
  assert.equal(store.getSnapshot().data,null);
  await store.refresh('U2');old.resolve(cases.F18);await first;
  assert.equal(store.getSnapshot().owner,'U2');assert.equal(store.getSnapshot().data,cases.F01);
});
test('failed reads clear the stale value, explain expiration and recover on retry',async()=>{
  let calls=0;
  const store=new AssetSummaryStore(async()=>{if(++calls===2)throw {response:{status:401}};return cases.F18;});
  await store.refresh('U1');await store.refresh('U1');
  assert.equal(store.getSnapshot().data,null);assert.match(store.getSnapshot().error,/登录已过期/);
  assert.equal(summaryTotal(store.getSnapshot().data,store.getSnapshot().error),'暂无法查询');
  await store.refresh('U1');assert.equal(store.getSnapshot().error,'');assert.equal(store.getSnapshot().data,cases.F18);
});
test('background polling keeps settled content stable, then publishes fresh facts',async()=>{
  const next=deferred<AssetSummary>();let calls=0;
  const store=new AssetSummaryStore(()=>++calls===1?Promise.resolve(cases.F01):next.promise);
  await store.refresh('U1');const before=store.getSnapshot();let updates=0;
  const unsubscribe=store.subscribe(()=>updates++);
  const poll=store.refresh('U1',{background:true});
  assert.equal(store.getSnapshot(),before);assert.equal(updates,0);
  assert.equal(store.getSnapshot().loading,false);
  next.resolve(cases.F18);await poll;unsubscribe();
  assert.equal(updates,1);assert.equal(store.getSnapshot().data,cases.F18);
});
test('background reads still report first load and failures; manual refresh signals progress',async()=>{
  const first=deferred<AssetSummary>(),failed=deferred<AssetSummary>(),manual=deferred<AssetSummary>();let calls=0;
  const store=new AssetSummaryStore(()=>[first,failed,manual][calls++].promise);
  const initial=store.refresh('U1',{background:true});assert.equal(store.getSnapshot().loading,true);
  first.resolve(cases.F18);await initial;
  const poll=store.refresh('U1',{background:true});failed.reject(new Error('offline'));await poll;
  assert.equal(store.getSnapshot().data,null);assert.match(store.getSnapshot().error,/查询失败/);
  const retry=store.refresh('U1');assert.equal(store.getSnapshot().loading,true);
  manual.resolve(cases.F18);await retry;
});
test('F10 displays missing valuation without turning known cash into the total',()=>{
  assert.equal(summaryTotal(cases.F10),'暂无法完整估值');
  assert.equal(summaryMoney(cases.F10.positions_market_value_fen),'缺少估值');
  assert.equal(summaryMoney(cases.F10.cash_balance_fen),'¥8,000.00');
  assert.equal(summaryMoney(cases.F10.redemption_receivable_fen),'¥500.00');
  assert.equal(summaryTotal(cases.F18),'¥10,000.00');
});
test('only successful asset-changing requests invalidate the shared reader',()=>{
  for(const path of ['/wealth/contexts/select','/wealth/previews/p1/execute','/wealth/orders/o1/cancel','/wealth/plans/p1/progress','/wealth/tasks/t1/progress'])assert.ok(changesAssetFacts('POST',path));
  for(const path of ['/wealth/demo/reset','/wealth/demo/adjust','/wealth/demo/advance','/wealth/demo/delete','/wealth/asset-summary','/wealth/assets','/wealth/demo/delete-preview','/wealth/previews','/wealth/previews/p1/confirm','/wealth/research'])assert.equal(changesAssetFacts('POST',path),false);
  assert.equal(changesAssetFacts('GET','/wealth/plans/p1/progress'),false);
  let refreshed=0;const unsubscribe=subscribeAssetSummaryChanges(()=>refreshed++);
  invalidateAssetSummary();unsubscribe();invalidateAssetSummary();assert.equal(refreshed,1);
});
