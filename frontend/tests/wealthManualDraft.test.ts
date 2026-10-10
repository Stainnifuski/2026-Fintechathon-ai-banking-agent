import test from 'node:test';
import assert from 'node:assert/strict';
import {ManualDraftStore,draftScope} from '../src/services/wealthManualDraft.ts';

test('two consumers share inputs, not authorization or action results',()=>{
  const store=new ManualDraftStore(),key=draftScope('u','c',['a']);store.bind(key,'c');
  let notifications=0;const stop=store.subscribe(()=>notifications++);
  store.update(key,'value','123.45');store.update(key,'inputMode','market_value');
  store.update(key,'selected',{type:'positions',ids:['original-position']});
  store.update(key,'ratios',{planKey:'plan:v1:snapshot',values:{p:'20'}});
  assert.equal(store.read(key,'c').value,'123.45');
  assert.deepEqual(store.read(key,'c').selected.ids,['original-position']);
  assert.equal(store.read(key,'c').ratios?.values.p,'20');assert.equal(notifications,4);
  for(const name of ['consent','code','preview','session','results','token'])assert.equal(name in store.read(key,'c'),false);
  stop();
});

test('subject, context and authorized account changes discard old draft; stale writers cannot restore it',()=>{
  const store=new ManualDraftStore();const original=draftScope('u','c',['a','b']);
  assert.equal(original,draftScope('u','c',['b','a']));
  for(const changed of [draftScope('v','c',['a','b']),draftScope('u','d',['a','b']),draftScope('u','c',['a'])]){
    store.bind(original,'c');store.update(original,'value','99');store.bind(changed,'d');
    store.update(original,'value','stale');assert.equal(store.read(changed,'d').value,'');
    assert.equal(store.read(original,'c').value,'');
    store.bind(original,'c');assert.equal(store.read(original,'c').value,'');
  }
});

test('logout clear drops draft and rejects late updates until fresh facts bind',()=>{
  const store=new ManualDraftStore(),key=draftScope('u','c',['a']);store.bind(key,'c');
  store.update(key,'value','9');store.clear();store.update(key,'value','late');
  assert.equal(store.read(key,'c').value,'');store.bind(key,'c');assert.equal(store.read(key,'c').value,'');
});
