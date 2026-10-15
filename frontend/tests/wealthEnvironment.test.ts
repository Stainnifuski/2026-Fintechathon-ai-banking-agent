import test from 'node:test';
import assert from 'node:assert/strict';
import {competitionEnvironment} from '../src/utils/wealthEnvironment.ts';
const current={context_id:'current',label:'比赛理财',mode:'snapshot',dataset_key:'real-public-paper-v1:current'};
const old={context_id:'legacy-v1',label:'原银行账本（兼容8产品）',mode:'legacy'};
test('old selection defaults to existing competition scope and removes old option',()=>{
 const value=competitionEnvironment({selected:'legacy-v1',version:4,items:[old,current]});
 assert.equal(value.selected,'current');assert.equal(value.version,4);assert.deepEqual(value.items,[current]);
});
test('current owned scope is preserved; archived and unrelated scopes never enter formal entry',()=>{
 const value=competitionEnvironment({selected:'current',version:5,items:[old,current,{...current,context_id:'archived',run_state:'ARCHIVED'},{...current,context_id:'other',dataset_key:'synthetic:v1'}]});
 assert.deepEqual(value.items,[current]);assert.equal(value.selected,'current');
});
test('missing competition environment fails explicitly rather than reverting to legacy',()=>{
 assert.throws(()=>competitionEnvironment({selected:'legacy-v1',version:1,items:[old]}),/尚无可用比赛理财环境/);
});
