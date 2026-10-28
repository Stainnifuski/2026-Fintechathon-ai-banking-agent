import assert from 'node:assert/strict';
import test from 'node:test';
import {selectHandoffCandidate} from '../src/components/novaHandoffCandidate.ts';

test('multiple unfinished cards allow handoff of the latest card when none is focused',()=>{
  const items=[
    {meta:{key:'wealth:old',session:'current',finished:true}},
    {meta:{key:'wealth:positions',session:'current'}},
    {meta:{key:'wealth:planning',session:'current'}},
  ];
  assert.equal(selectHandoffCandidate(items,'current',null)?.meta.key,'wealth:planning');
  assert.equal(selectHandoffCandidate(items,'current','wealth:positions')?.meta.key,'wealth:positions');
  assert.equal(selectHandoffCandidate(items,'other',null),undefined);
});

test('released, completed and uncertain cards cannot be handed off again',()=>{
  const items=[
    {meta:{key:'wealth:open',session:'current'}},
    {meta:{key:'wealth:released',session:'current',released:{mode:'manual'}}},
    {meta:{key:'wealth:uncertain',session:'current'},uncertain:true},
    {meta:{key:'wealth:done',session:'current'},completed:true},
  ];
  assert.equal(selectHandoffCandidate(items,'current',null)?.meta.key,'wealth:open');
});
