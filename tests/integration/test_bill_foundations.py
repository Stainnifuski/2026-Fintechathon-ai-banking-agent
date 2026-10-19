"""Bill identity/link evidence, ownership, persistence and forecast regression tests."""
from datetime import datetime, timezone
import httpx
import pytest
from sqlalchemy import select, func
from tests.integration.test_bills import db, tx, NOW, QUERY
from tests.integration.test_bill_enhancements import sub, ledger_counts
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.bank_operation import BankOperationORM as Operation
from backend.app.models.payment_request import PaymentRequestORM
from backend.app.models.payment_request_payment import PaymentRequestPaymentORM
from backend.app.models.merchant_refund import MerchantRefundORM
from backend.app.models.bill_foundation import BillTransactionMetaORM as Meta, BillMerchantORM as Merchant, BillTransferPairORM as Pair, BillRecurringStreamORM as Stream, BillRecurringOccurrenceORM as Occurrence, BillMetadataEventORM as Event
from backend.app.schemas.bills import BillQuery, BillAnalysis
from backend.app.schemas.bill_foundation import AnnotationUpdate, RecurringUpdate
from backend.app.schemas.bill_insights import CashflowScenario
from backend.app.services.bank import bill_foundation as foundation
from backend.app.services.bank.bill_recurring import list_recurring, set_enabled
from backend.app.services.bank.bill_service import analyze, transactions, TZ
from backend.app.services.bank.bill_cashflow import project_cashflow
from backend.app.services.bank.transfer_service import BankError


def op(key, debit, sender='A1', receiver='A2', amount=1000):
    return Operation(operation_id=key, status='SUCCEEDED', param_digest='x', from_account=sender, to_account=receiver, amount_fen=amount, currency='CNY', bank_tx_id=debit)


async def sync(db):
    result = await foundation.sync_users(db, ['U1','U2'], now=NOW)
    await db.commit()
    return result


@pytest.mark.asyncio
async def test_idempotent_backfill_preserves_raw_ledger_and_owned_identity(db):
    db.add_all([tx('one',100,counterparty=' Ｍusic  Store '),tx('two',200,counterparty='music store'),tx('foreign',100,account='A3',counterparty='music store')])
    await db.commit()
    before = await ledger_counts(db)
    await sync(db)
    ids = [(await db.get(Meta,key)).merchant_id for key in ['one','two','foreign']]
    assert ids[0] == ids[1] != ids[2]
    assert (await foundation.catalog(db,'U1'))['metadata_total'] == 2
    count = await db.scalar(select(func.count()).select_from(Merchant))
    await sync(db)
    assert await db.scalar(select(func.count()).select_from(Merchant)) == count
    assert await ledger_counts(db) == before
    assert (await db.get(TransactionORM,'one')).counterparty == ' Ｍusic  Store '


@pytest.mark.asyncio
async def test_verified_bank_identity_survives_merchant_rename(db):
    db.add_all([tx('a',1000,counterparty='旧商户',operation_id='O1'),op('O1','a',receiver='A3'),tx('b',1000,counterparty='新商户',operation_id='O2'),op('O2','b',receiver='A3')])
    await db.commit(); await sync(db)
    assert (await db.get(Meta,'a')).merchant_id == (await db.get(Meta,'b')).merchant_id
    assert len((await foundation.catalog(db,'U1'))['merchants']) == 1
    assert (await foundation.catalog(db,'U1'))['merchants'][0]['bank_verified']


@pytest.mark.asyncio
async def test_explicit_alias_and_merge_change_attribution_not_ledger(db):
    db.add_all([tx('old',100,counterparty='旧名',when='2026-08-10T04:00:00'),tx('new',200,counterparty='新名')])
    await db.commit(); await sync(db)
    target, source = (await db.get(Meta,'old')).merchant_id, (await db.get(Meta,'new')).merchant_id
    with pytest.raises(BankError) as conflict:
        await foundation.add_alias(db,'U1',target,'新名')
    assert conflict.value.code == 'ALIAS_CONFLICT'
    with pytest.raises(BankError):
        await foundation.merge_merchants(db,'U2',target,source)
    before = await ledger_counts(db)
    await foundation.merge_merchants(db,'U1',target,source)
    await db.commit()
    result=await analyze(db,'U1',QUERY,now=NOW)
    drivers=result['change_explanation']['drivers']
    assert len(drivers)==1 and drivers[0]['previous_count']==drivers[0]['current_count']==1
    assert (await db.get(Meta,'new')).merchant_id == target
    assert await ledger_counts(db)==before
    await foundation.add_alias(db,'U1',target,'第三名称'); await db.commit()
    db.add(tx('future',300,counterparty='第三名称')); await db.commit(); await sync(db)
    assert (await db.get(Meta,'future')).merchant_id == target


@pytest.mark.asyncio
async def test_conflicting_verified_merchants_cannot_merge(db):
    db.add_all([tx('one',1),tx('two',2,counterparty='另一个')]);await db.commit();await sync(db)
    a,b=(await db.get(Meta,'one')).merchant_id,(await db.get(Meta,'two')).merchant_id
    (await db.get(Merchant,a)).bank_identity='bank-one'
    (await db.get(Merchant,b)).bank_identity='bank-two'
    await db.commit()
    with pytest.raises(BankError) as error:
        await foundation.merge_merchants(db,'U1',a,b)
    assert error.value.code=='MERCHANT_IDENTITY_CONFLICT'


async def refund_chain(db):
    db.add_all([tx('original',1000,category='dining',operation_id='purchase-op',when='2026-08-10T04:00:00'),tx('refund',1000,'refund'),tx('unlinked',200,'refund'),op('purchase-op','original',receiver='A3')])
    db.add(PaymentRequestORM(payment_request_id='payment',requester_user_id='U2',payer_user_id='U1',receive_account_id='A3',amount_fen=1000,currency='CNY',status='PAID',idempotency_key='payment-key',expires_at=NOW))
    await db.flush()
    db.add(PaymentRequestPaymentORM(payment_request_id='payment',operation_id='purchase-op',payer_account_id='A1'))
    db.add(MerchantRefundORM(payment_request_id='payment',operation_id='refund-op',merchant_account_id='A3',customer_account_id='A1',amount_fen=1000,merchant_tx_id='merchant-refund',customer_tx_id='refund',status='SUCCEEDED'))
    await db.commit()


@pytest.mark.asyncio
async def test_refund_proof_arrival_period_net_category_and_unlinked(db):
    await refund_chain(db);await sync(db)
    result=await analyze(db,'U1',QUERY,now=NOW)
    assert result['totals']['linked_refund_fen']==1000 and result['totals']['unlinked_refund_fen']==200
    cat=result['categories'][0]
    assert cat['category']=='dining' and cat['refund_fen']==1000 and cat['net_amount_fen']==-1000
    assert result['previous_totals']['consumption_fen']==1000
    filtered=await analyze(db,'U1',BillQuery(date='2026-09',category='dining'),now=NOW)
    assert filtered['totals']['refund_fen']==1000 and filtered['totals']['consumption_fen']==0
    page=await transactions(db,'U1',QUERY,as_of=NOW)
    assert next(t for t in page['items'] if t['tx_id']=='refund')['original_tx_id']=='original'
    assert next(t for t in page['items'] if t['tx_id']=='refund')['original_category']=='dining'
    assert (await foundation.metadata_views(db,'U1',['original']))['original']['refund_tx_ids']==['refund']
    assert next(t for t in page['items'] if t['tx_id']=='unlinked')['original_tx_id'] is None
    BillAnalysis.model_validate(result)


@pytest.mark.asyncio
async def test_refund_cannot_link_to_other_customers_account(db):
    await refund_chain(db)
    (await db.get(TransactionORM,'original')).account_id='A3';await db.commit();await sync(db)
    assert (await db.get(Meta,'refund')).original_tx_id is None


@pytest.mark.asyncio
async def test_self_transfer_pairs_exact_legs_and_scope(db):
    db.add_all([tx('out',1000,'transfer_out',counterparty='A2',operation_id='internal'),tx('in',1000,'transfer_in',account='A2',counterparty='A1'),op('internal','out')])
    await db.commit();await sync(db)
    meta=await foundation.metadata_views(db,'U1',['out','in'])
    assert meta['out']['paired_tx_id']=='in' and meta['in']['paired_tx_id']=='out'
    assert meta['out']['transfer_pair_id']==meta['in']['transfer_pair_id']
    assert (await analyze(db,'U1',QUERY,now=NOW))['totals']['count']==0
    single=await transactions(db,'U1',BillQuery(date='2026-09',account_id='A1'),as_of=NOW)
    assert single['total']==1 and single['items'][0]['paired_tx_id']=='in'
    assert await foundation.metadata_views(db,'U2',['out','in'])=={}
    await sync(db)
    assert await db.scalar(select(func.count()).select_from(Pair))==1


@pytest.mark.asyncio
async def test_ambiguous_historical_transfer_not_guessed_but_writer_pair_is_exact(db):
    db.add_all([tx('out',1000,'transfer_out',counterparty='A2',operation_id='internal'),tx('in1',1000,'transfer_in',account='A2',counterparty='A1'),tx('in2',1000,'transfer_in',account='A2',counterparty='A1'),op('internal','out')])
    await db.commit();await sync(db)
    assert (await db.get(Meta,'out')).transfer_pair_id is None
    await foundation.sync_users(db,['U1'],now=NOW,exact_pair=('out','in2'));await db.commit()
    assert (await foundation.metadata_views(db,'U1',['out']))['out']['paired_tx_id']=='in2'
    assert (await db.get(Meta,'in1')).transfer_pair_id is None


@pytest.mark.asyncio
async def test_tags_filter_version_audit_and_ownership(db):
    db.add_all([tx('one',100),tx('two',200),tx('foreign',999,account='A3')]);await db.commit();await sync(db)
    before=await ledger_counts(db)
    saved=await foundation.annotate(db,'U1','one',AnnotationUpdate(tags=['出差','可报销'],expected_version=0));await db.commit()
    assert saved['annotation_version']==1
    tag=saved['tags'][0]['tag_id']
    result=await analyze(db,'U1',BillQuery(date='2026-09',tag_id=tag),now=NOW)
    assert result['totals']['consumption_fen']==100
    with pytest.raises(BankError) as stale:
        await foundation.annotate(db,'U1','one',AnnotationUpdate(tags=['新标签'],expected_version=0))
    assert stale.value.code=='STALE_ANNOTATION'
    for action in [foundation.annotate(db,'U2','one',AnnotationUpdate(tags=['劫持'],expected_version=1)), analyze(db,'U2',BillQuery(date='2026-09',tag_id=tag),now=NOW)]:
        with pytest.raises(BankError) as denied: await action
        assert denied.value.status_code==404
    assert await db.scalar(select(func.count()).select_from(Event))==1
    assert await ledger_counts(db)==before
    await foundation.annotate(db,'U1','one',AnnotationUpdate(tags=[],expected_version=1));await db.commit()
    assert (await foundation.metadata_views(db,'U1',['one']))['one']['tags']==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('names',[['a','Ａ'],['   ']])
async def test_tags_reject_normalized_duplicates_and_blank(db,names):
    db.add(tx('one',100));await db.commit()
    with pytest.raises(BankError) as error:
        await foundation.annotate(db,'U1','one',AnnotationUpdate(tags=names,expected_version=0))
    assert error.value.status_code==422


async def recurring_history(db, *, duplicate=False, month_end=False):
    for month in (7,8,9):
        day=31 if month in (7,8) else 30
        when=f'2026-{month:02}-{day if month_end else 25:02}T04:00:00'
        db.add(tx(f'salary{month}',100000,'salary',counterparty='雇主',when=when))
        db.add(tx(f'rent{month}',30000,category='dining',counterparty='房租',when=f'2026-{month:02}-20T04:00:00'))
        for d in (1,2,3):
            db.add(tx(f'food{month}{d}',3000,counterparty='餐厅',when=f'2026-{month:02}-{d:02}T04:00:00'))
    if duplicate: db.add(tx('bonus',100,'salary',counterparty='雇主',when='2026-09-25T04:01:00'))
    await db.commit()


@pytest.mark.asyncio
async def test_persisted_recurring_evidence_preferences_and_no_double_count(db):
    await recurring_history(db);await sync(db)
    items=(await list_recurring(db,'U1'))['items']
    salary=next(i for i in items if i['kind']=='income');rent=next(i for i in items if i['kind']=='expense')
    assert salary['enabled'] and not rent['enabled']
    assert salary['sample_count']==3 and len(salary['evidence_tx_ids'])==3
    before=await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert before['estimated_income_fen']==100000 and before['estimated_recurring_spending_fen']==0
    await set_enabled(db,'U1',rent['stream_id'],RecurringUpdate(enabled=True,expected_version=rent['version']));await db.commit()
    after=await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert after['estimated_recurring_spending_fen']==30000
    assert after['estimated_variable_spending_fen']<before['estimated_variable_spending_fen']
    assert after['spending_baselines'][0]['sample_count']==9  # rent observations removed
    assert after['baseline_end_fen']==after['opening_available_fen']+100000-30000-after['estimated_variable_spending_fen']
    await sync(db)
    assert (await db.get(Stream,rent['stream_id'])).enabled
    await set_enabled(db,'U1',salary['stream_id'],RecurringUpdate(enabled=False,expected_version=salary['version']));await db.commit()
    assert (await project_cashflow(db,'U1',CashflowScenario(),now=NOW))['estimated_income_fen']==0
    assert await db.scalar(select(func.count()).select_from(Occurrence))==6
    with pytest.raises(BankError):
        await set_enabled(db,'U2',rent['stream_id'],RecurringUpdate(enabled=False,expected_version=99))


@pytest.mark.asyncio
async def test_month_end_income_and_stale_detection(db):
    await recurring_history(db,month_end=True);await sync(db)
    salary=next(i for i in (await list_recurring(db,'U1'))['items'] if i['kind']=='income')
    assert salary['month_end']
    forecast=await project_cashflow(db,'U1',CashflowScenario(),now=NOW)
    assert next(e for e in forecast['events'] if e['source']=='salary_estimate')['at'].day==31
    await foundation.sync_users(db,['U1'],now=datetime(2026,12,1,tzinfo=TZ));await db.commit()
    assert (await db.get(Stream,salary['stream_id'])).status=='STALE'
    assert (await project_cashflow(db,'U1',CashflowScenario(),now=datetime(2026,12,1,tzinfo=TZ)))['estimated_income_fen']==0


@pytest.mark.asyncio
async def test_duplicate_month_does_not_create_salary_stream(db):
    await recurring_history(db,duplicate=True);await sync(db)
    assert all(i['kind']!='income' for i in (await list_recurring(db,'U1'))['items'])


@pytest.mark.asyncio
async def test_official_subscription_stream_cancelled_and_ambiguous_charges(db):
    db.add_all([sub(),sub('S2'),tx('charge',1500,'subscription',counterparty='音乐')]);await db.commit();await sync(db)
    assert (await db.get(Meta,'charge')).recurring_stream_id is None
    item=(await list_recurring(db,'U1'))['items'][0]
    with pytest.raises(BankError) as error:
        await set_enabled(db,'U1',item['stream_id'],RecurringUpdate(enabled=False,expected_version=item['version']))
    assert error.value.code=='SUBSCRIPTION_CONFIRMATION_REQUIRED'


@pytest.mark.asyncio
async def test_refresh_and_annotations_rollback_atomically(db):
    db.add(tx('one',100));await db.commit()
    await foundation.sync_users(db,['U1'],now=NOW)
    await foundation.annotate(db,'U1','one',AnnotationUpdate(tags=['暂存'],expected_version=0))
    await db.rollback()
    assert await db.get(Meta,'one') is None
    assert await db.scalar(select(func.count()).select_from(Event))==0
    assert await db.get(TransactionORM,'one') is not None


@pytest.mark.asyncio
async def test_http_contract_auth_and_nova_metadata(db):
    from backend.app.main import app
    from backend.app.database.connection import get_db
    from backend.app.middleware.auth import create_access_token
    from backend.app.services.ai.nova import execute_tool
    from backend.app.services.ai.nova_tools import ToolContext
    from backend.app.schemas.nova import PageContext, BillFilters
    db.add_all([tx('one',100),tx('foreign',900,account='A3')]);await db.commit()
    async def override():yield db
    app.dependency_overrides[get_db]=override
    headers={'Authorization':'Bearer '+create_access_token({'sub':'U1'})}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.post('/api/v1/bills/foundations/refresh')).status_code==401
            response=await client.post('/api/v1/bills/foundations/refresh',headers=headers)
            assert response.status_code==200,response.text
            response=await client.put('/api/v1/bills/transactions/one/annotations',headers=headers,json={'tags':['测试'],'expected_version':0})
            assert response.status_code==200,response.text
            tag=response.json()['tags'][0]['tag_id']
            assert (await client.get('/api/v1/bills/transactions/foreign',headers=headers)).status_code==404
            assert (await client.put('/api/v1/bills/transactions/foreign/annotations',headers=headers,json={'tags':[],'expected_version':0})).status_code==404
            assert (await client.put('/api/v1/bills/transactions/one/annotations',headers=headers,json={'tags':[],'expected_version':1,'user_id':'U2'})).status_code==422
            assert (await client.get('/api/v1/bills/analysis',params={'date':'2026-09','tag_id':tag},headers=headers)).json()['totals']['consumption_fen']==100
        context=ToolContext('U1',db,PageContext(page='insights',filters=BillFilters(date='2026-09',tag_id=tag)))
        result=await execute_tool({'function':{'name':'get_transaction','arguments':'{"tx_id":"one"}'}},context)
        assert result['data']['tags'][0]['name']=='测试'
        result=await execute_tool({'function':{'name':'list_recurring_records','arguments':'{}'}},context)
        assert result['data']['items']==[]
        from backend.app.services.ai.nova import offline_business
        reply=await offline_business('我的周期收支记录',context)
        assert reply['tool_calls'][0]['name']=='list_recurring_records'
    finally:app.dependency_overrides.pop(get_db,None)


@pytest.mark.asyncio
async def test_file_database_foreign_keys_and_concurrent_annotation_versions(tmp_path):
    """Two real sessions cannot overwrite a version already committed by another editor."""
    import asyncio
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.app.models import Base, UserORM
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'bill.db'))
    @event.listens_for(engine.sync_engine,'connect')
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(Base.metadata.create_all)  # additive startup is repeatable
        async with sessions() as session:
            session.add(UserORM(user_id='U1',name='甲',phone='13800000001',id_card='demo',password_hash='hash',status='active'));await session.flush()
            session.add(AccountORM(account_id='A1',user_id='U1',account_type='savings',card_number='one',balance_fen=10000));await session.flush()
            session.add(tx('one',100));await session.commit()
            await foundation.sync_users(session,['U1'],now=NOW);await session.commit()
        async def edit(name):
            async with sessions() as session:
                try:
                    result=await foundation.annotate(session,'U1','one',AnnotationUpdate(tags=[name],expected_version=0))
                    await asyncio.sleep(.02)
                    await session.commit()
                    return result['annotation_version']
                except BankError as error:
                    await session.rollback()
                    return error.code
        outcomes=await asyncio.gather(edit('first'),edit('second'))
        assert set(outcomes)=={1,'STALE_ANNOTATION'}
        async with sessions() as session:
            assert len((await foundation.metadata_views(session,'U1',['one']))['one']['tags'])==1
            assert await session.scalar(select(func.count()).select_from(Event))==1
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_card_writer_metadata_shares_ledger_rollback(db):
    from backend.app.models import CardORM
    from backend.app.services.bank.card_service import simulate_card_purchase
    db.add(CardORM(card_id='C1',user_id='U1',account_id='A1',card_type='DEBIT',is_virtual=False,card_number='1234',status='ACTIVE',single_txn_limit_fen=100000,daily_txn_limit_fen=100000,single_txn_cap_fen=100000,daily_txn_cap_fen=100000))
    await db.commit()
    result=await simulate_card_purchase(db,user_id='U1',card_id='C1',operation_id='card-test',amount_fen=100,merchant='测试商户',channel='pos')
    assert result['authorization']['approved']
    tx_id=result['authorization']['tx_id']
    assert (await db.get(Meta,tx_id)).merchant_id
    await db.rollback()
    assert await db.get(TransactionORM,tx_id) is None
    assert await db.get(Meta,tx_id) is None
    assert (await db.get(AccountORM,'A1')).balance_fen==100000


@pytest.mark.asyncio
async def test_bank_identity_beats_preexisting_unverified_name_alias(db):
    # Two historical labels are not proof of one merchant; new bank-backed payments are.
    db.add_all([tx('unknown-a',100,counterparty='名称甲'),tx('unknown-b',100,counterparty='名称乙')])
    await db.commit();await sync(db)
    db.add_all([tx('known-a',1000,counterparty='名称甲',operation_id='known-op-a'),op('known-op-a','known-a',receiver='A3'),tx('known-b',1000,counterparty='名称乙',operation_id='known-op-b'),op('known-op-b','known-b',receiver='A3')])
    await db.commit();await sync(db)
    assert (await db.get(Meta,'known-a')).merchant_id == (await db.get(Meta,'known-b')).merchant_id
    assert (await db.get(Meta,'unknown-a')).merchant_id != (await db.get(Meta,'unknown-b')).merchant_id
