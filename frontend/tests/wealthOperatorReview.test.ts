import test from 'node:test';
import assert from 'node:assert/strict';
import {adjustmentNote,checkOperatorReview,reviewCommands,operatorPortfolio,portfolioAdjustmentNote} from '../src/services/wealthOperatorReview.ts';
import fixture from '../../tests/fixtures/wealth_portfolio_contract_v1/f11.json' with {type:'json'};
import candidates from '../../tests/fixtures/wealth_portfolio_contract_v1/f09.json' with {type:'json'};
import type {PortfolioPlan} from '../src/services/wealth.ts';
import type {ReviewCommand,OperatorContext} from '../src/services/wealthOperatorReview.ts';
import type {WealthTask,Assets} from '../src/services/wealth.ts';
const rows:ReviewCommand[]=[
  {command:{operation_id:'buy-original',direction:'purchase',product_id:'p1',product_version:1,disclosure_version:2,account_id:'a',amount_fen:10000,units:'0.0000'},status:'WAITING'},
  {command:{operation_id:'sell-original',direction:'redeem',product_id:'p2',product_version:3,disclosure_version:4,account_id:'a',amount_fen:0,units:'10.0000'},status:'READY'},
];
const task:WealthTask={task_id:'t',session_id:'s',account_id:'a',context_id:'c',version:3,status:'WAITING',control_mode:'HUMAN',owner_id:'operator',waiting_reason:null,goal:'purchase',notes:[],nodes:rows.map((r,i)=>({node_id:`n${i}`,kind:'SUBMIT',status:r.status,depends_on:[],operation_id:r.command.operation_id,command:r.command}))};
const context:OperatorContext={task,conversation:null,plan:null};
const facts={context_id:'c',cash:[{account_id:'a'}]} as Assets;

test('operator allocation restores all exact products and rejects missing candidates or revoked accounts',()=>{
  const plan={...fixture.plan,candidate_groups:candidates.groups} as PortfolioPlan;
  const ctx={...context,task:{...task,context_id:plan.context_id,account_id:fixture.snapshot.account_ids[0]},portfolio:{plan,snapshot:fixture.snapshot,notice:''}};
  const bankFacts={...facts,context_id:plan.context_id,cash:fixture.snapshot.account_ids.map(account_id=>({account_id}))} as Assets;
  const view=operatorPortfolio(ctx,bankFacts)!;
  assert.equal(view.selection.items.length,7);assert.equal(view.selection.scopeKey,plan.asset_scope_id);
  assert.throws(()=>operatorPortfolio(ctx,{...bankFacts,cash:[]}));
  assert.throws(()=>operatorPortfolio({...ctx,portfolio:{...ctx.portfolio,plan:{...plan,candidate_groups:[]}}},bankFacts));
  assert.equal(operatorPortfolio({...ctx,portfolio:null},bankFacts),null);
});
test('overall adjustment stays an unvalidated suggestion bound to plan/version/snapshot and never mutates the plan',()=>{
  const plan=fixture.plan as PortfolioPlan;const before=JSON.stringify(plan);
  const input={portfolio_id:plan.portfolio_id,expected_version:plan.version,snapshot_id:plan.snapshot_id,context_id:plan.context_id,items:plan.selected_positions};
  assert.match(portfolioAdjustmentNote(plan,input),/未校验、未修改方案、未授权/);
  assert.equal(JSON.stringify(plan),before);
  for(const changed of [{...input,expected_version:plan.version+1},{...input,snapshot_id:'foreign'},{...input,items:[]},{...input,items:input.items.map((p,i)=>i? p:{...p,target_ratio_total:'1.1'})}])assert.throws(()=>portfolioAdjustmentNote(plan,changed));
});

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
