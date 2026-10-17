import test from 'node:test';
import assert from 'node:assert/strict';
import {WealthActionSession} from '../src/services/wealthActionSession.ts';
import type {ActionRequest,ActionResult} from '../src/services/wealthActions';
const result=(request:ActionRequest,version:number):ActionResult=>({schema_version:'wealth-action-result-v1',request_id:request.request_id,context_id:request.context_id,session_id:'WC-session',session_version:version,action:request.action,status:'ok',message:'完成',data:[],required_fields:[],allowed_actions:[]});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(yes=>resolve=yes);return {promise,resolve};}

test('detail and comparison requests share a serialized session version',async()=>{
  const gate=deferred<ActionResult>();const requests:ActionRequest[]=[];
  const client=new WealthActionSession(async request=>{requests.push(request);return requests.length===1?gate.promise:result(request,3);});
  client.setContext('one');
  const first=client.request('show_product_detail',{product_id:'P1',product_version:1});
  const second=client.request('compare_products',{product_ids:['P1','P2']});
  await Promise.resolve();assert.equal(requests.length,1);
  gate.resolve(result(requests[0],2));await first;await second;
  assert.equal(requests[0].session_id,undefined);
  assert.equal(requests[1].expected_session_version,2);assert.equal(requests[1].session_id,'WC-session');
  assert.notEqual(requests[0].request_id,requests[1].request_id);
});

test('scope switch rejects in-flight results and queued old work without restoring its session',async()=>{
  const gate=deferred<ActionResult>();const requests:ActionRequest[]=[];
  const client=new WealthActionSession(async request=>{requests.push(request);return requests.length===1?gate.promise:result(request,2);});
  client.setContext('old');
  const first=client.request('show_product_detail',{});const queued=client.request('compare_products',{});
  const rejectedFirst=assert.rejects(first,/资金范围/),rejectedQueue=assert.rejects(queued,/资金范围/);
  await Promise.resolve();client.setContext('new');gate.resolve(result(requests[0],2));
  await rejectedFirst;await rejectedQueue;
  await client.request('list_positions',{page:1,page_size:5});
  assert.equal(requests.length,2);assert.equal(requests[1].context_id,'new');assert.equal(requests[1].session_id,undefined);
});

test('a failed preview is not automatically retried and does not strand later reads',async()=>{
  const requests:ActionRequest[]=[];
  const client=new WealthActionSession(async request=>{requests.push(request);if(request.action==='preview_redemption')throw new Error('断线');return result(request,2);});
  client.setContext('one');await assert.rejects(client.request('preview_redemption',{}),/断线/);
  await client.request('list_orders',{page:1,page_size:5});assert.equal(requests.length,2);
});

test('unexpected response scope never updates the session',async()=>{
  let count=0;const requests:ActionRequest[]=[];
  const client=new WealthActionSession(async request=>{requests.push(request);return {...result(request,2),context_id:++count===1?'wrong':request.context_id};});
  client.setContext('one');await assert.rejects(client.request('show_product_detail',{}),/资金范围/);
  await client.request('list_positions',{});assert.equal(requests[1].session_id,undefined);
});

test('bucket-only slider revision omits empty optional selection context',async()=>{
 const requests:ActionRequest[]=[];
 const client=new WealthActionSession(async request=>{requests.push(request);return result(request,2);});
 client.setContext('one');
 const selection={portfolio_id:'plan-one',expected_version:1,snapshot_id:'snapshot-one',context_id:'one',
   revision_kind:'adjustment',growth_ratio_ex_cash:'0.8',items:[]};
 await client.request('revise_portfolio',{selection},{object_type:'products',ids:[]});
 assert.equal(requests[0].selection_context,undefined);
 assert.deepEqual(requests[0].params.selection,selection);
 // Actual selected product identities still travel unchanged for server checks.
 await client.request('revise_portfolio',{selection},{object_type:'products',ids:['000003:4']});
 assert.deepEqual(requests[1].selection_context,{object_type:'products',ids:['000003:4']});
});
