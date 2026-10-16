import test from 'node:test';
import assert from 'node:assert/strict';
import {initialTableState,currentTableState,selectTablePage} from '../src/components/Wealth/tableState.ts';
import {ManualDraftStore,draftScope} from '../src/services/wealthManualDraft.ts';

test('orders and positions preserve separate cross-page choices, sizes and page numbers',()=>{
  const store=new ManualDraftStore(),key=draftScope('u','context',['a']);store.bind(key,'context');
  const rows=Array.from({length:12},(_,i)=>({id:`order-${i}`}));
  let orders=selectTablePage(initialTableState('scope'),rows,row=>row.id,true);
  orders={...orders,page:2};orders=selectTablePage(orders,rows,row=>row.id,true);
  store.update(key,'orderTable',orders);
  store.update(key,'positionTable',{...initialTableState('scope'),pageSize:10,selected:['position-1']});
  assert.equal(store.read(key,'context').orderTable.page,2);
  assert.equal(store.read(key,'context').orderTable.selected.length,10);
  assert.equal(store.read(key,'context').positionTable.pageSize,10);
  assert.deepEqual(store.read(key,'context').positionTable.selected,['position-1']);
  orders=selectTablePage(orders,rows,row=>row.id,false);
  assert.deepEqual(orders.selected,rows.slice(0,5).map(row=>row.id));
});

test('fresh complete rows prune removed references and clamp page; scope changes clear state',()=>{
  const state={scope:'old',page:3,pageSize:5,selected:['still','gone']};
  const current=currentTableState(state,'old',[{id:'still'}],row=>row.id);
  assert.equal(current.page,1);assert.deepEqual(current.selected,['still']);
  assert.deepEqual(currentTableState(state,'new',[{id:'still'}],row=>row.id),initialTableState('new'));
});

test('scope/logout clears both shared tables and rejects old callbacks',()=>{
  const store=new ManualDraftStore(),key=draftScope('u','c',['a']);store.bind(key,'c');
  store.update(key,'orderTable',{...initialTableState('scope'),selected:['order']});
  store.update(key,'positionTable',{...initialTableState('scope'),selected:['position']});
  const changed=draftScope('u','d',['b']);store.bind(changed,'d');
  store.update(key,'orderTable',{...initialTableState('scope'),selected:['late']});
  assert.deepEqual(store.read(changed,'d').orderTable.selected,[]);
  assert.deepEqual(store.read(changed,'d').positionTable.selected,[]);
  store.clear();assert.deepEqual(store.read(changed,'d').positionTable.selected,[]);
});
