"""Member 2 event callbacks verify existing bank facts; never credit by assertion."""
from datetime import timedelta
from uuid import uuid4
import pytest
from tests.integration.test_wealth_bank import store,http_store,assess,NOW
from backend.app.models import TransactionORM,AccountORM


@pytest.mark.asyncio
async def test_external_callback_requires_registered_owned_bank_fact_and_replays(store,http_store,monkeypatch):
    client,_,_=http_store
    async with store() as db:
        await assess(db)
        db.add(TransactionORM(tx_id='rent-paid-event',account_id='A1',operation_id='rent-operation',
            type='transfer_out',amount_fen=100000,counterparty='虚构房东',category='other',note='',
            timestamp=NOW-timedelta(hours=1),status='completed',channel='app'))
        db.add(TransactionORM(tx_id='future-paid-event',account_id='A1',operation_id='future-rent',
            type='transfer_out',amount_fen=100000,counterparty='虚构房东',category='other',note='',
            timestamp=NOW+timedelta(days=1),status='completed',channel='app'))
        await db.commit()
    request={'context_id':'legacy-v1','request_id':uuid4().hex,'parent_task_id':'parent-rent',
        'external_dependencies':['rent_completed'],'raw_text':'投入可用现金的一半，半年后用，兼顾波动和增长',
        'parsed_intent':{'intent':'allocate','semantic':{'patches':[
            {'field':'investment_budget','op':'set','quote':'投入可用现金的一半','budget':{'mode':'proportion','ratio':'0.5','base':'available_cash'}},
            {'field':'horizon','op':'set','quote':'半年后用','horizon':{'unit':'months','count':6,'text':'半年后用'}},
            {'field':'investment_goal','op':'set','quote':'兼顾波动和增长','goal':'balanced'}]}}}
    created=(await client.post('/api/v1/wealth/subtasks',json=request)).json()
    assert created['waiting_reason']=='EXTERNAL_EVENT'
    task_id=created['child_task_id'];node=created['result']['nodes'][0]['node_id']
    callback={'request_id':uuid4().hex,'expected_version':1,'parent_task_id':'parent-rent',
        'node_id':node,'event_reference':'made-up-paid'}
    bad=await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json=callback)
    assert bad.status_code==409
    missing=await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json={**callback,'node_id':'nonexistent'})
    assert missing.status_code==409
    forged=await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json={**callback,'paid':True})
    assert forged.status_code==422
    future=await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json={**callback,'event_reference':'future-paid-event'})
    assert future.status_code==409
    wrong_parent=await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json={**callback,'parent_task_id':'unregistered-parent'})
    assert wrong_parent.status_code==409
    # A completed bank transfer changed actual cash; the callback itself never credits it.
    async with store() as db:
        account=await db.get(AccountORM,'A1');account.balance_fen=1900000;await db.commit()
    callback['event_reference']='rent-paid-event'
    done=await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json=callback)
    assert done.status_code==200,done.text
    continuation=done.json()['continuation']
    assert continuation['cards']['plan']['request']['budget_fen']==950000
    assert (await client.post('/api/v1/wealth/subtasks/'+task_id+'/external-completed',json=callback)).json()==done.json()
    async with store() as db:assert (await db.get(AccountORM,'A1')).balance_fen==1900000
