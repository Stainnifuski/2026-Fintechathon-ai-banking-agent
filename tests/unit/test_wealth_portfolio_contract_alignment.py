"""00四项边界的受控事实验收；不是可信银行正向Plan证明。"""
from copy import deepcopy
from datetime import timedelta
import pytest
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import NOW,fixture_pool,at
from tests.unit.test_wealth_portfolio_lifecycle import create,edit,state
from tests.unit.test_wealth_portfolio_bank_facts import run_reads
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.schemas.wealth_portfolio_candidates import CurrentSimulationEvidence
from backend.app.domain.wealth_portfolio_eligibility import qualify_purchase,version_context
from backend.app.integrations.wealth_portfolio_bank_facts import BankPortfolioFacts,BankPortfolioAccess


def test_snapshot_node_attempts_not_authority_or_asset_versions():
    from backend.app.integrations.wealth_portfolio_bank_facts import snapshot_business_content
    first={'tasks':{'items':[{'task_id':'t','version':3,'control_mode':'AGENT','session_closed':False,'nodes':[{'attempts':1}]}]},'assets':{'cash':100}}
    second=deepcopy(first);second['tasks']['items'][0]['nodes'][0]['attempts']=2
    assert snapshot_business_content(first)==snapshot_business_content(second)
    for field,value in [('version',4),('control_mode','HUMAN'),('session_closed',True)]:
        changed=deepcopy(second);changed['tasks']['items'][0][field]=value
        assert snapshot_business_content(first)!=snapshot_business_content(changed)
    second['assets']['cash']=101
    assert snapshot_business_content(first)!=snapshot_business_content(second)


@pytest.mark.asyncio
@pytest.mark.parametrize('epoch',[None,1])
async def test_null_and_integer_without_old_session_create_read(epoch):
    service,*_=lifecycle_fixture()
    service.access.grant['context_epoch']=epoch
    service.access.grant['selection_version']=6
    service.access.grant.pop('session_ref')
    service.provider.asset_source.bundle['context_epoch']=epoch
    plan=await create(service,'.625')
    assert plan.model_view.snapshot.context_epoch==epoch
    # 读取确实没有可编辑证据也能读；写入仍拒绝，旧方案不变。
    service.access.grant.pop('can_edit')
    assert (await service.get('fixture-user',plan.portfolio_id,'fixture-context')).version==1
    before=state(service,plan)
    with pytest.raises(CandidateError) as error:await service.revise('fixture-user',plan.portfolio_id,edit(plan))
    assert error.value.code=='PORTFOLIO_WRITE_CONTROL_UNPROVEN' and state(service,plan)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['selection_version','context_epoch','asset_scope_id','account_ids','session_closed'])
async def test_stale_or_closed_binding_read_and_revision_atomic(change):
    service,*_=lifecycle_fixture();service.access.grant['selection_version']=6
    plan=await create(service,'.625');before=state(service,plan)
    if change=='selection_version':service.access.grant[change]=8 # away then back has new switch version
    elif change=='context_epoch':service.access.grant[change]=2
    elif change=='asset_scope_id':service.access.grant[change]='other-scope'
    elif change=='account_ids':service.access.grant[change]=['another-account']
    else:service.access.grant[change]=True
    with pytest.raises(CandidateError):await service.get('fixture-user',plan.portfolio_id,'fixture-context')
    with pytest.raises(CandidateError):await service.revise('fixture-user',plan.portfolio_id,edit(plan))
    assert state(service,plan)==before


@pytest.mark.asyncio
async def test_null_without_trusted_switch_stays_closed():
    service,*_=lifecycle_fixture();service.access.grant['context_epoch']=None
    with pytest.raises(CandidateError) as error:await service._access('fixture-user','fixture-context',False)
    assert error.value.code=='PORTFOLIO_CONTEXT_UNPROVEN'


@pytest.mark.asyncio
async def test_bank_positive_epoch_is_preserved_not_replaced_by_switch_version():
    reads=run_reads()
    selected=next(r for r in reads.values['/api/v1/wealth/contexts']['items'] if r['context_id']=='run-a')
    selected['context_epoch']=2
    source=BankPortfolioFacts(reads,'data','daily')
    assert (await source.read('test-subject'))['context_epoch']==2
    grant=await BankPortfolioAccess(source).resolve('test-subject','run-a',write=False)
    assert grant['context_epoch']==2 and grant['selection_version']==6


@pytest.mark.asyncio
async def test_bank_read_scope_no_old_task_or_edit_fields():
    reads=run_reads()
    reads.values['/api/v1/wealth/tasks']={'items':[{'context_id':'run-a','status':'RUNNING','control_mode':'HUMAN'}]}
    access=BankPortfolioAccess(BankPortfolioFacts(reads,'data','daily'))
    grant=await access.resolve('test-subject','run-a',write=False)
    assert grant['can_read'] and 'can_edit' not in grant and 'session_ref' not in grant
    assert grant['context_epoch'] is None and grant['selection_version']==6
    assert '/api/v1/wealth/tasks' not in reads.calls
    with pytest.raises(CandidateError) as error:await access.resolve('test-subject','run-a',write=True)
    assert error.value.code=='TASK_WRITE_BLOCKED'


@pytest.mark.asyncio
@pytest.mark.parametrize('flag',['session_closed','stop_new_submissions'])
async def test_related_original_lock_not_bypassed(flag):
    reads=run_reads();task={'task_id':'related','plan_id':'known-plan','context_id':'run-a',
        'status':'RUNNING','control_mode':'AGENT','version':3,'session_closed':False,'stop_new_submissions':False}
    task[flag]=True;reads.values['/api/v1/wealth/tasks']={'items':[task]}
    access=BankPortfolioAccess(BankPortfolioFacts(reads,'data','daily'))
    with pytest.raises(CandidateError):await access.resolve('test-subject','run-a',write=True,portfolio_id='known-plan')
    if flag=='session_closed':
        with pytest.raises(CandidateError):await access.resolve('test-subject','run-a',write=False,portfolio_id='known-plan')
    else:
        assert (await access.resolve('test-subject','run-a',write=False,portfolio_id='known-plan'))['can_read']


def current_simulation():
    snapshot,facts=fixture_pool(count=1)
    product=snapshot.products[0].model_copy(deep=True)
    product.source_kind='simulated';product.fields['formal_risk'].status='simulated'
    facts.mode='simulated';facts.source_id='protected-local-bank';facts.channel='local-competition-bank'
    facts.assessment.source_id=facts.source_id;facts.assessment.source_kind='simulated'
    offer=facts.offers[product.product_id]
    offer.source_id=facts.source_id;offer.source_kind='simulated';offer.channel=facts.channel
    offer.valid_from=offer.valid_until=None
    offer.current_simulation=CurrentSimulationEvidence(provenance='protected-local-bank-current-products',
        product_id=product.product_id,product_version=1,disclosure_version=1,observed_at=NOW,
        business_at=at('2026-10-08T03:00:00Z'),calendar_version='cn-trading-2021-2027-v1',
        cutoff='15:00',open_interval_days=1,nav='1',nav_date='2026-10-08',
        available_at=at('2026-10-08T02:00:00Z'),status='on_sale',purchase_paused=False,purchase_open=True,data_stale=False)
    return product,facts,offer


def test_current_simulated_rules_no_permanent_window():
    product,facts,offer=current_simulation()
    decision=qualify_purchase(product,facts,'fixture-user',NOW)
    assert decision.purchase_eligible and offer.valid_from is None and offer.valid_until is None
    assert decision.user_level==2 and decision.product_level==2


@pytest.mark.parametrize('change,reason',[
    ('paused','PURCHASE_STOPPED'),('stale','BANK_DATA_STALE'),('future_nav','BANK_FUTURE_DATA'),
    ('future_available','BANK_FUTURE_DATA'),('expired_sale','BANK_PURCHASE_PERIOD_ENDED'),
    ('future_read','BANK_CURRENT_READ_TIME_UNPROVEN'),('wrong_version','BANK_CURRENT_EVIDENCE_MISMATCH'),
    ('calendar','BANK_BUSINESS_RULES_UNPROVEN'),('wrong_channel','BANK_FACT_TIME_UNPROVEN'),
    ('explicit_expired','BANK_FACT_TIME_UNPROVEN'),('risk','RISK_EXCEEDS_CAPACITY'),
    ('missing_rules','CURRENT_RULES_INCOMPLETE'),('closed_day','BANK_NON_OPEN_DAY'),
    ('window_future_nav','BANK_FUTURE_DATA')])
def test_current_simulation_retains_time_risk_and_rule_gates(change,reason):
    product,facts,offer=current_simulation();e=offer.current_simulation
    if change=='paused':e.purchase_paused=True
    if change=='stale':e.data_stale=True
    if change=='future_nav':e.nav_date=NOW.date()
    if change=='future_available':e.available_at=at('2026-10-08T04:00:00Z')
    if change=='expired_sale':e.purchase_end_date=at('2026-10-07T00:00:00Z').date()
    if change=='future_read':e.observed_at=NOW+timedelta(seconds=1)
    if change=='wrong_version':e.product_version=2
    if change=='calendar':e.calendar_version='unverified'
    if change=='wrong_channel':offer.channel='unrelated'
    if change=='explicit_expired':offer.valid_from=NOW-timedelta(days=2);offer.valid_until=NOW-timedelta(days=1)
    if change=='risk':product.fields['formal_risk'].value=4;offer.risk_level=4
    if change=='missing_rules':offer.rules_complete=False
    if change=='closed_day':e.open_interval_days=100
    if change=='window_future_nav':
        offer.valid_from=NOW-timedelta(days=1);offer.valid_until=NOW+timedelta(days=1);e.nav_date=NOW.date()
    decision=qualify_purchase(product,facts,'fixture-user',NOW)
    assert not decision.purchase_eligible and reason in decision.reason_codes


def test_real_product_never_inherits_simulation_time_exemption():
    product,facts,offer=current_simulation();product.source_kind='real'
    decision=qualify_purchase(product,facts,'fixture-user',NOW)
    assert not decision.purchase_eligible
    assert {'PRODUCT_RISK_UNKNOWN','BANK_FACT_TIME_UNPROVEN'}<=set(decision.reason_codes)


def test_observation_refresh_does_not_invent_validity_or_change_content_version():
    _,facts,offer=current_simulation();before=version_context(facts)
    facts.read_at+=timedelta(seconds=1);facts.assessment.read_at=facts.read_at
    offer.current_simulation.observed_at=facts.read_at
    assert before==version_context(facts)
    assert offer.valid_from is None and offer.valid_until is None


@pytest.mark.asyncio
async def test_legacy_named_context_and_cursor_away_back_rejected():
    from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioRecommendation,CandidateBinding
    from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
    service,*_=lifecycle_fixture()
    service.access.grant.update(context_id='legacy-v1',context_epoch=None,selection_version=6)
    service.access.grant.pop('session_ref')
    service.provider.asset_source.bundle.update(context_id='legacy-v1',context_epoch=None)
    group=await service.recommend('fixture-user',PortfolioRecommendation(context_id='legacy-v1',filter=CandidateFilter(mode='general')))
    session=group.session_binding
    assert session and session['cursor']
    request=PortfolioRecommendation(context_id='legacy-v1',filter=CandidateFilter(mode='general'),cursor=session['cursor'],
        candidate_binding=CandidateBinding(session_id=session['session_id'],expected_version=session['version'],filter=CandidateFilter(mode='general')))
    service.access.grant['selection_version']=8
    with pytest.raises(CandidateError,match='PORTFOLIO_CANDIDATE_BINDING_STALE'):
        await service.recommend('fixture-user',request)
