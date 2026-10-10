import test from 'node:test';
import assert from 'node:assert/strict';
import recording from '../../tests/fixtures/wealth_portfolio_contract_v1/f09.json' with {type:'json'};
import {readCandidateGroup,assertCurrentCandidates,sameCandidateFilter,replaceCandidateGroup} from '../src/services/wealthCandidates.ts';
import {emptyProductSelection,toggleProduct,selectionForScope} from '../src/components/Wealth/productSelection.ts';
import type {Product} from '../src/services/wealth';

test('shared F09 groups and missing business states are consumable without fabricating metrics',()=>{
  for(const raw of [...recording.groups,recording.risk_group,recording.unavailable_group])assert.equal(readCandidateGroup(raw).status,raw.status);
  const optional=readCandidateGroup({bucket:null,filter:{mode:'category',category:'bond_fund'},status:'no_category_match',items:[]});
  assert.equal(optional.next_cursor,null);assert.equal(optional.risk_explanation,null);
  assert.equal(readCandidateGroup(recording.groups[0]).items[0].share_classes[0].performance.latest_day.value_percent,null);
});
test('malformed provider responses, unknown status and duplicate families are rejected',()=>{
  const group=recording.groups[0];
  for(const invalid of [null,{...group,items:[{}]},{...group,status:'unsupported'}, {...group,items:[group.items[0],group.items[0]]},
    {...group,items:[{...group.items[0],share_classes:[{...group.items[0].share_classes[0],fees:null}]}]}])assert.throws(()=>readCandidateGroup(invalid));
});
test('catalog version mismatch and changed filter cannot become current candidates',()=>{
  const group=readCandidateGroup(recording.groups[0]);const share=group.items[0].share_classes[0];
  assertCurrentCandidates(group,[{product_id:share.product_id,version:share.product_version} as Product]);
  assert.throws(()=>assertCurrentCandidates(group,[{product_id:share.product_id,version:share.product_version+1} as Product]));
  assert.equal(sameCandidateFilter({mode:'general'},{mode:'general',category:null,bucket:null}),true);
  assert.equal(sameCandidateFilter({mode:'category',category:'bond_fund'},{mode:'category',category:'equity_fund'}),false);
});
test('replacement preserves other groups and selection across batches; scope switch clears',()=>{
  const groups=recording.groups.map(readCandidateGroup);const stable=groups.find(g=>g.bucket==='stable')!;
  const selected=toggleProduct(emptyProductSelection('scope-A'),stable.items[0]);
  const next=readCandidateGroup({...stable,items:[stable.items[1]],next_cursor:'page-2'});
  const replaced=replaceCandidateGroup(groups,next);
  assert.equal(replaced.length,groups.length);assert.equal(replaced.find(g=>g.bucket==='cash_management'),groups[0]);
  assert.equal(selectionForScope(selected,'scope-A').items[0].candidate.family_id,stable.items[0].family_id);
  assert.equal(selectionForScope(selected,'scope-B').items.length,0);
});
