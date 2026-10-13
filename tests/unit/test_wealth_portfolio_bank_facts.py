"""受控来源检查；不是当前银行事实接通证明。"""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from backend.app.integrations.wealth_portfolio_bank_facts import BankPortfolioFacts, BankPortfolioAccess, LocalBankReads, business_content
from backend.app.services.orchestration.wealth_portfolio import SnapshotError
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.services.orchestration.wealth_portfolio_data import digest

class Reads:
    def __init__(self):
        self.values = {'/api/v1/auth/me': {'user_id': 'test-subject'},
            '/api/v1/wealth/assets': {'cash': [], 'lots': [], 'as_of': '2026-10-09T10:00:00Z'},
            '/api/v1/wealth/assessments/current': None, '/api/v1/wealth/products': [],
            '/api/v1/wealth/contexts': {'selected': 'legacy-v1'}, '/api/v1/wealth/tasks': {'items': []}}
        self.calls = []
    async def get(self, path):
        self.calls.append(path)
        return deepcopy(self.values[path])

@pytest.mark.asyncio
async def test_legacy_not_full_summary():
    reads = Reads(); source = BankPortfolioFacts(reads, 'data', 'daily')
    with pytest.raises(SnapshotError) as error: await source.read('test-subject')
    assert error.value.code == 'ASSET_SUMMARY_UNAVAILABLE'
    assert len(reads.calls) == 12

@pytest.mark.asyncio
async def test_subject_rejected_before_business_reads():
    reads = Reads()
    with pytest.raises(CandidateError) as error: await BankPortfolioFacts(reads, 'data', 'daily').observe('another')
    assert error.value.status_code == 403 and len(reads.calls) == 1

@pytest.mark.asyncio
async def test_unknown_assessment_not_fabricated():
    facts = await BankPortfolioFacts(Reads(), 'data', 'daily').read_current('test-subject', datetime.now(timezone.utc))
    assert facts.assessment is None and not facts.assessment_current_proven and facts.offers == {}

@pytest.mark.asyncio
async def test_raw_risk_preserved_permission_unknown():
    reads = Reads()
    reads.values['/api/v1/wealth/products'] = [{'product_id': 'R4-C', 'version': 2,
        'product_risk_level': 4, 'rules': {'min_amount_fen': 100}, 'disclosure': {'version': 2}}]
    facts = await BankPortfolioFacts(reads, 'data', 'daily').read_current('test-subject', datetime.now(timezone.utc))
    offer = facts.offers['R4-C']
    assert offer.risk_level == 4 and offer.purchase_allowed is None and offer.valid_until is None
    assert not offer.rules_complete and offer.risk_current_confirmed
    assert offer.risk_evidence_id.startswith('bank-current-simulated:')

@pytest.mark.asyncio
async def test_access_missing_control_closed():
    reads = run_reads()
    reads.values['/api/v1/wealth/tasks'] = {'items':[{'context_id':'run-a','status':'RUNNING','control_mode':'AGENT'}]}
    with pytest.raises(CandidateError) as error:
        await BankPortfolioAccess(BankPortfolioFacts(reads, 'data', 'daily')).resolve('test-subject', 'run-a', write=True)
    assert error.value.code == 'PORTFOLIO_WRITE_CONTROL_UNPROVEN'

@pytest.mark.asyncio
async def test_cross_context_rejected():
    with pytest.raises(CandidateError) as error:
        await BankPortfolioAccess(BankPortfolioFacts(Reads(), 'data', 'daily')).resolve('test-subject', 'foreign', write=False)
    assert error.value.status_code == 403

def test_content_stamp_observation_only():
    first = {'as_of': 'a', 'business_at': '2026-10-09T10:00:00Z', 'cash': 100}
    second = {**first, 'as_of': 'b', 'business_at': '2026-10-09T10:01:00Z'}
    assert digest(business_content(first)) == digest(business_content(second))
    assert digest(business_content(first)) != digest(business_content({**second, 'cash': 101}))

@pytest.mark.parametrize('url', ['https://example.com', 'http://example.com', 'http://localhost@evil.test', 'http://127.0.0.1/a'])
def test_loopback_only(url):
    with pytest.raises(ValueError): LocalBankReads(url, 'controlled-test-token')

@pytest.mark.asyncio
async def test_change_between_reads_rejected():
    class Change(Reads):
        async def get(self, path):
            value = await super().get(path)
            if path.endswith('/assets'): value['cash'] = len(self.calls)
            return value
    with pytest.raises(SnapshotError) as error: await BankPortfolioFacts(Change(), 'data', 'daily').read('test-subject')
    assert error.value.code == 'BANK_FACTS_CHANGED'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,control', [('PAUSED','AGENT'), ('RUNNING','HUMAN'), ('RUNNING',None)])
async def test_visible_control_denial_preserved(status,control):
    reads = run_reads()
    reads.values['/api/v1/wealth/tasks'] = {'items': [{'context_id':'run-a','status':status,'control_mode':control}]}
    with pytest.raises(CandidateError) as error:
        await BankPortfolioAccess(BankPortfolioFacts(reads,'data','daily')).resolve('test-subject','run-a',write=True)
    assert error.value.code == 'TASK_WRITE_BLOCKED'

@pytest.mark.asyncio
async def test_bad_source_not_no_products():
    reads = Reads(); reads.values['/api/v1/wealth/products'] = {'items':[]}
    with pytest.raises(CandidateError) as error:
        await BankPortfolioFacts(reads,'data','daily').read_current('test-subject',datetime.now(timezone.utc))
    assert error.value.code == 'BANK_READ_SCHEMA_INVALID'

@pytest.mark.asyncio
async def test_assessment_identity_version_and_confirmation_mapping():
    reads = Reads()
    reads.values['/api/v1/wealth/assessments/current'] = {'assessment_id':'controlled-assessment',
        'profile_version':3,'capacity_level':2,'confirmed':False,'purchase_allowed':True,
        'expires_at':'2026-01-01T00:00:00Z'}
    now = datetime.now(timezone.utc)
    facts = await BankPortfolioFacts(reads,'data','daily').read_current('test-subject',now)
    from backend.app.domain.wealth_portfolio_eligibility import assessment_reasons
    reasons = assessment_reasons(facts,'test-subject',now)
    assert {'ASSESSMENT_UNCONFIRMED','ASSESSMENT_EXPIRED','ASSESSMENT_CURRENT_VERSION_UNPROVEN'} <= set(reasons)
    assert 'ASSESSMENT_SOURCE_MISMATCH' not in reasons
    assert facts.assessment.capacity_level == 2 and facts.assessment.version.startswith('adapter:')

@pytest.mark.asyncio
async def test_independent_rules_not_blocked_by_missing_assets():
    reads = Reads(); reads.values['/api/v1/wealth/assets'] = None
    facts = await BankPortfolioFacts(reads,'data','daily').read_current('test-subject',datetime.now(timezone.utc))
    assert facts.assessment is None
    assert '/api/v1/wealth/assets' not in reads.calls and len(reads.calls) == 3

def test_naive_business_time_not_host_timezone():
    with pytest.raises(ValueError): business_content({'business_at':'2026-10-09T12:00:00'})
    assert business_content({'business_at':'2026-10-09T20:00:00Z'}) == {'business_at':'2026-10-10'}


@pytest.mark.parametrize('status,paused,opened,stale,expected', [
    ('on_sale', False, True, False, True), ('stopped', False, True, False, False),
    ('on_sale', True, True, False, False), ('on_sale', False, False, False, False),
    ('on_sale', False, True, True, False), ('on_sale', None, True, False, None),
    (None, False, True, False, None)])
def test_native_sale_not_missing_alias(status,paused,opened,stale,expected):
    from backend.app.integrations.wealth_portfolio_bank_facts import native_product_evidence
    row = {'product_id':'controlled','version':4,'product_risk_level':4,
        'status':status,'purchase_open':opened,'data_stale':stale,'rules':{'purchase_paused':paused}}
    actual = native_product_evidence(row)
    assert actual['purchase_allowed'] is expected
    assert actual['risk_current_confirmed']
    assert not actual['rules_complete']


@pytest.mark.parametrize('risk,version', [(None,4),(True,4),(6,4),(4,None),(4,True)])
def test_unknown_native_risk_never_confirmed(risk,version):
    from backend.app.integrations.wealth_portfolio_bank_facts import native_product_evidence
    actual = native_product_evidence({'product_id':'controlled','version':version,'product_risk_level':risk})
    assert not actual['risk_current_confirmed'] and actual['risk_evidence_id'] is None


@pytest.mark.asyncio
async def test_malformed_native_rules_rejected():
    reads = Reads()
    reads.values['/api/v1/wealth/products'] = [{'product_id':'controlled','rules':None}]
    with pytest.raises(CandidateError) as error:
        await BankPortfolioFacts(reads,'data','daily').read_current('test-subject',datetime.now(timezone.utc))
    assert error.value.code == 'BANK_READ_SCHEMA_INVALID'


def test_native_completeness_needs_disclosure_and_supported_calendar():
    from backend.app.integrations.wealth_portfolio_bank_facts import native_product_evidence
    rules = {'currency':'CNY','min_amount_fen':100,'max_amount_fen':1000000,
        'purchase_fee_rate':'0','redeem_fee_bands':[{'days_lt':1000000,'rate':'0'}],
        'holding_days':0,'confirm_business_days':1,'cash_business_days':1,'cutoff':'15:00',
        'calendar_version':'cn-trading-2021-2027-v1','cancel_before_pricing':True,
        'purchase_paused':False,'redeem_paused':False}
    disclosure = {'nature':'模拟净值型','manager':'虚构管理人','seller':'模拟银行','risk':'R2',
        'fees':'见模拟规则','liquidity':'普通赎回','holding_period_days':0,'pricing':'T日净值',
        'settlement':'模拟T+1','cancellation':'定价前','limitations':'仅比赛模拟'}
    row = {'product_id':'controlled','rules':rules,'disclosure':disclosure}
    assert native_product_evidence(row)['rules_complete']
    incomplete = deepcopy(row); del incomplete['disclosure']['limitations']
    assert not native_product_evidence(incomplete)['rules_complete']
    unsupported = deepcopy(row); unsupported['rules']['calendar_version'] = 'unknown'
    assert not native_product_evidence(unsupported)['rules_complete']


@pytest.mark.asyncio
async def test_native_mapping_does_not_fabricate_validity_share_or_profile():
    reads = Reads()
    reads.values['/api/v1/wealth/products'] = [{'product_id':'controlled','version':4,
        'product_risk_level':4,'status':'on_sale','purchase_open':True,'data_stale':False,
        'rules':{'purchase_paused':False},'disclosure':{'version':4}}]
    facts = await BankPortfolioFacts(reads,'data','daily').read_current('test-subject',datetime.now(timezone.utc))
    offer = facts.offers['controlled']
    assert offer.purchase_allowed and offer.risk_current_confirmed
    assert offer.share_class is None and offer.valid_from is None and offer.valid_until is None
    assert not facts.assessment_current_proven and offer.product_version == 4


def run_reads():
    reads = Reads()
    reads.values['/api/v1/wealth/contexts'] = {'selected':'run-a','version':6,'items':[
        {'context_id':'run-a','account_id':'debit-a','run_id':'run-a','run_state':'ACTIVE','disposable':True},
        {'context_id':'peer-a','account_id':'debit-b','run_id':'run-a','run_state':'ACTIVE','disposable':True},
        {'context_id':'other-run','account_id':'excluded','run_id':'other-run','run_state':'ACTIVE','disposable':True}]}
    reads.values['/api/v1/wealth/assets'] = {'context_id':'run-a','scope':'disposable_simulation',
        'as_of':'2026-10-09T10:00:00Z','business_at':'2026-10-09T10:00:00Z',
        'cash':[{'account_id':'debit-a','account_type':'savings','balance_fen':600000,'held_fen':100000,'available_fen':500000},
            {'account_id':'debit-b','account_type':'checking','balance_fen':200000,'held_fen':0,'available_fen':200000}],
        'lots':[{'lot_id':'controlled-lot','account_id':'debit-b','product_id':'controlled','product_version':1,
            'units':'1500.0000','redeemable_units':'1500.0000','market_value_fen':150000,'nav':'1','nav_date':'2026-10-09','category':'bond'}],
        'orders':[{'operation_id':'controlled-order','account_id':'debit-a','direction':'redeem','status':'PENDING_CASH',
            'details':{'actual':{'cash_fen':50000}}}],'bank_assets_fen':1000000,'receivable_fen':50000}
    return reads


@pytest.mark.asyncio
async def test_full_run_scope_and_d01_derived_without_native_version():
    from backend.app.services.orchestration.wealth_portfolio import build_snapshot
    bundle = await BankPortfolioFacts(run_reads(),'data','daily').read('test-subject')
    snapshot = build_snapshot(bundle)
    assert snapshot.basis_fen == 1000000 and sum(a.available_fen for a in snapshot.accounts) == 700000
    assert snapshot.account_ids == ['debit-a','debit-b'] and snapshot.context_epoch is None
    assert snapshot.snapshot_id.startswith('adapter:') and snapshot.basis_status == 'confirmed'


@pytest.mark.asyncio
async def test_raw_unknown_nav_not_replaced_by_bank_zero_aggregate():
    from backend.app.services.orchestration.wealth_portfolio import build_snapshot
    reads = run_reads(); raw = reads.values['/api/v1/wealth/assets']
    raw['lots'][0].update(market_value_fen=None,nav=None,nav_date=None)
    raw['bank_assets_fen'] = 850000
    snapshot = build_snapshot(await BankPortfolioFacts(reads,'data','daily').read('test-subject'))
    assert snapshot.basis_fen is None and not snapshot.valuation_complete
    assert snapshot.positions[0].market_value_fen is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation,expected', [('omit_peer','ASSET_SCOPE_UNPROVEN'),
    ('foreign_lot','ASSET_SCOPE_FORBIDDEN'),('archived_peer','ASSET_SCOPE_UNPROVEN')])
async def test_scope_equivalence_requires_complete_owned_active_set(mutation,expected):
    reads = run_reads()
    if mutation == 'omit_peer': reads.values['/api/v1/wealth/assets']['cash'].pop()
    if mutation == 'foreign_lot': reads.values['/api/v1/wealth/assets']['lots'][0]['account_id']='excluded'
    if mutation == 'archived_peer': reads.values['/api/v1/wealth/contexts']['items'][1]['run_state']='ARCHIVED'
    with pytest.raises(SnapshotError) as error:
        await BankPortfolioFacts(reads,'data','daily').read('test-subject')
    assert error.value.code == expected


@pytest.mark.asyncio
async def test_snapshot_digest_tracks_amount_not_observation_time():
    reads = run_reads(); source=BankPortfolioFacts(reads,'data','daily')
    first=await source.read('test-subject')
    reads.values['/api/v1/wealth/assets']['as_of']='2026-10-09T10:01:00Z'
    second=await source.read('test-subject')
    assert first['summary']['snapshot_id']==second['summary']['snapshot_id']
    reads.values['/api/v1/wealth/assets']['cash'][1].update(balance_fen=200001,available_fen=200001)
    third=await source.read('test-subject')
    assert first['summary']['snapshot_id']!=third['summary']['snapshot_id']
