import test from 'node:test';
import assert from 'node:assert/strict';
import {adjustmentNote,checkOperatorReview,reviewCommands} from '../src/services/wealthOperatorReview.ts';
import type {ReviewCommand,OperatorContext} from '../src/services/wealthOperatorReview.ts';
import type {WealthTask,Assets} from '../src/services/wealth.ts';
const rows:ReviewCommand[]=[
  {command:{operation_id:'buy-original',direction:'purchase',product_id:'p1',product_version:1,disclosure_version:2,account_id:'a',amount_fen:10000,units:'0.0000'},status:'WAITING'},
  {command:{operation_id:'sell-original',direction:'redeem',product_id:'p2',product_version:3,disclosure_version:4,account_id:'a',amount_fen:0,units:'10.0000'},status:'READY'},
];
const task:WealthTask={task_id:'t',session_id:'s',account_id:'a',context_id:'c',version:3,status:'WAITING',control_mode:'HUMAN',owner_id:'operator',waiting_reason:null,goal:'purchase',notes:[],nodes:rows.map((r,i)=>({node_id:`n${i}`,kind:'SUBMIT',status:r.status,depends_on:[],operation_id:r.command.operation_id,command:r.command}))};
const context:OperatorContext={task,conversation:null,plan:null};
const facts={context_id:'c',cash:[{account_id:'a'}]} as Assets;

test('adjustment is text tied to original identity; it cannot change commands or produce authority',()=>{
  const before=JSON.stringify(rows);const note=adjustmentNote(rows,{'buy-original':'250.25','sell-original':'1.1234'});
  assert.match(note,/未修改订单、未授权/);assert.match(note,/buy-original/);assert.match(note,/250.25 元/);assert.match(note,/1.1234 份/);
  assert.equal(JSON.stringify(rows),before);
});
test('invalid precision, zero, overflow and absent object cannot become a review note',()=>{
  for(const v of ['0','-1','1.001','NaN','20000000.01'])assert.throws(()=>adjustmentNote(rows,{'buy-original':v}));
  for(const v of ['0','1.00001','Infinity'])assert.throws(()=>adjustmentNote(rows,{'sell-original':v}));
  assert.throws(()=>adjustmentNote(rows,{'unknown-operation':'10'}));
});
test('review is bound to claiming operator, original task/version/account/context',()=>{
  assert.equal(checkOperatorReview(task,'operator',facts,context),context);
  for(const changed of [{...task,version:4},{...task,task_id:'another'},{...task,account_id:'other'},{...task,context_id:'d'},{...task,control_mode:'AGENT' as const},{...task,owner_id:'other'}])assert.throws(()=>checkOperatorReview(task,'operator',facts,{...context,task:changed}));
  assert.throws(()=>checkOperatorReview(task,'other',facts,context));
  assert.throws(()=>checkOperatorReview(task,'operator',{...facts,context_id:'other'},context));
  assert.throws(()=>checkOperatorReview(task,'operator',{...facts,cash:[]},context));
});
test('shared review uses persisted plan commands or original submit nodes without inventing orders',()=>{
  assert.deepEqual(reviewCommands(context),rows);
  assert.deepEqual(reviewCommands({...context,plan:{plan_id:'plan',version:1,status:'WAITING',data:{items:[rows[0]]}}}),[rows[0]]);
  assert.deepEqual(reviewCommands({...context,task:{...task,nodes:[]}}),[]);
});
