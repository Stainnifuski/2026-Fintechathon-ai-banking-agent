"""01资格/匹配/家族/候选状态验收，全部为显式服务器fixture。"""
import asyncio
from copy import deepcopy
from datetime import timedelta
import pytest
from backend.app.schemas.wealth_portfolio_candidates import (CandidateFilter,SelectionRequest,SessionSelectionRequest,SwitchRequest,NextBatchRequest,SessionAction)
from backend.app.schemas.wealth_portfolio_data import FieldFact
from backend.app.domain.wealth_portfolio_eligibility import qualify_purchase,version_context
from backend.app.domain.wealth_portfolio_candidates import (ProductIndex,pipeline,theme_tags,issuer_identity)
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError,CandidateService,SnapshotIndexCache
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,service,share,field,NOW

run=asyncio.run
GENERAL=CandidateFilter(mode='general')
THEME=CandidateFilter(mode='theme',theme_key='high_dividend')

def test_current_bank_observation_time_does_not_change_pool_but_rules_do():
    from backend.app.schemas.wealth_portfolio_candidates import CurrentSimulationEvidence
    snapshot,facts=fixture_pool(2)
    facts.mode='simulated';facts.source_id='protected-local-bank';facts.channel='local-competition-bank'
    facts.assessment.source_kind='simulated';facts.assessment.source_id=facts.source_id
    for p in snapshot.products:
        p.source_kind='simulated';p.fields['formal_risk'].status='simulated'
        for evidence in p.family_evidence:evidence.source_kind='simulated'
        offer=facts.offers[p.product_id];offer.source_kind='simulated';offer.source_id=facts.source_id;offer.channel=facts.channel
        offer.valid_from=None;offer.valid_until=None
        offer.current_simulation=CurrentSimulationEvidence(provenance='protected-local-bank-current-products',
            product_id=p.product_id,product_version=p.product_version,disclosure_version=p.disclosure_version,
            observed_at=NOW,business_at=NOW,calendar_version='mock-cn-2026-v1',cutoff='15:00',open_interval_days=1,
            nav_date=NOW.date(),available_at=NOW,nav='1.2000',status='on_sale',purchase_paused=False,purchase_open=True,data_stale=False)
    index=ProductIndex(snapshot);first=pipeline(index,GENERAL,facts,facts.user_id,NOW)
    assert first.family_candidates
    later=NOW+timedelta(seconds=1);facts.read_at=later;facts.assessment.read_at=later
    for offer in facts.offers.values():offer.current_simulation.observed_at=later;offer.current_simulation.business_at=later
    assert pipeline(index,GENERAL,facts,facts.user_id,later).pool_version==first.pool_version
    facts.offers[next(iter(facts.offers))].rules_version='changed-version'
    assert pipeline(index,GENERAL,facts,facts.user_id,later).pool_version!=first.pool_version

def test_immutable_index_interns_only_unknown_constants_without_changing_content():
    from backend.app.services.orchestration.wealth_portfolio_data import digest
    from pydantic import ValidationError
    data,_=fixture_pool(1)
    for p in data.products:p.fields['unknown-probe']=FieldFact(status='missing',reason='source did not provide it')
    expected=digest([p.model_dump(mode='json') for p in data.products])
    index=ProductIndex(data,immutable_source=True)
    a,b=data.products[:2]
    assert a.fields['unknown-probe'] is b.fields['unknown-probe']
    assert index.content_fingerprint==expected and index.interned_unknown_fields>0
    with pytest.raises(ValidationError):a.fields['unknown-probe'].value='invented'
    with pytest.raises(TypeError):a.fields['unknown-probe'].observations.append(None)

def test_mutable_injected_index_keeps_independent_unknown_fields():
    data,_=fixture_pool(1)
    for p in data.products:p.fields['unknown-probe']=FieldFact(status='missing',reason='same missing reason')
    index=ProductIndex(data)
    assert index.interned_unknown_fields==0
    assert data.products[0].fields['unknown-probe'] is not data.products[1].fields['unknown-probe']

def risk(snapshot,facts,code,level):
    next(p for p in snapshot.products if p.product_id==code).fields['formal_risk']=field(level,'risk_level')
    facts.offers[code].risk_level=level

@pytest.mark.parametrize('user',[1,2,3,4,5])
@pytest.mark.parametrize('product',[1,2,3,4,5])
def test_risk_matrix_reaches_comparison(user,product):
    snapshot,facts=fixture_pool(1,user,product)
    d=qualify_purchase(snapshot.products[0],facts,'fixture-user',NOW)
    assert d.user_level==user and d.product_level==product
    assert d.purchase_eligible==(product<=user)
    assert d.reason_codes==([] if product<=user else ['RISK_EXCEEDS_CAPACITY'])
    assert d.holding_allowed is True and d.redemption_allowed is True
    assert d.amount_executable is None and d.amount_check_status=='not_checked'

@pytest.mark.parametrize('change,reason',[
 ({'confirmed':False},'ASSESSMENT_UNCONFIRMED'),({'purchase_allowed':False},'ASSESSMENT_PURCHASE_FORBIDDEN'),
 ({'expires_at':NOW},'ASSESSMENT_EXPIRED'),({'expires_at':NOW-timedelta(seconds=1)},'ASSESSMENT_EXPIRED'),
 ({'expires_at':None},'ASSESSMENT_EXPIRY_UNKNOWN'),({'capacity_level':None},'ASSESSMENT_LEVEL_INVALID'),
 ({'capacity_level':6},'ASSESSMENT_LEVEL_INVALID'),({'user_id':'another'},'ASSESSMENT_IDENTITY_MISMATCH'),
 ({'version':2},'ASSESSMENT_VERSION_STALE'),({'version':None},'ASSESSMENT_VERSION_UNKNOWN'),
 ({'source_id':'unknown'},'ASSESSMENT_SOURCE_MISMATCH'),({'read_at':NOW-timedelta(seconds=1)},'ASSESSMENT_READ_TIME_MISMATCH')])
def test_assessment_gate(change,reason):
    snapshot,facts=fixture_pool(1);facts.assessment=facts.assessment.model_copy(update=change)
    d=qualify_purchase(snapshot.products[0],facts,'fixture-user',NOW)
    assert not d.purchase_eligible and reason in d.reason_codes and d.user_level is None

@pytest.mark.parametrize('change,reason',[
 ({'assessment':None},'ASSESSMENT_REQUIRED'),({'assessment_current_proven':False},'ASSESSMENT_CURRENT_VERSION_UNPROVEN'),
 ({'read_at':NOW-timedelta(seconds=1)},'FACT_READ_TIME_MISMATCH'),({'latest_assessment_version':2},'ASSESSMENT_VERSION_STALE'),
 ({'user_id':'another'},'IDENTITY_MISMATCH')])
def test_context_gate(change,reason):
    snapshot,facts=fixture_pool(1);facts=facts.model_copy(update=change)
    assert reason in qualify_purchase(snapshot.products[0],facts,'fixture-user',NOW).reason_codes

@pytest.mark.parametrize('change,reason',[
 ({'purchase_allowed':False},'PURCHASE_STOPPED'),({'purchase_allowed':None},'PURCHASE_STATUS_UNKNOWN'),
 ({'rules_complete':False},'CURRENT_RULES_INCOMPLETE'),({'minimum_fen':None},'PURCHASE_MINIMUM_UNKNOWN'),
 ({'maximum_fen':None},'PURCHASE_LIMIT_UNKNOWN'),({'rules_version':None},'BANK_RULES_VERSION_UNKNOWN'),
 ({'valid_until':NOW},'BANK_FACT_TIME_UNPROVEN'),({'channel':'other-bank'},'BANK_CHANNEL_MISMATCH'),
 ({'share_class':'C'},'BANK_SHARE_IDENTITY_MISMATCH'),({'product_version':2},'BANK_PRODUCT_VERSION_MISMATCH'),
 ({'disclosure_version':2},'BANK_DISCLOSURE_VERSION_MISMATCH'),({'risk_current_confirmed':False},'CURRENT_RISK_UNPROVEN')])
def test_current_bank_rules_required(change,reason):
    snapshot,facts=fixture_pool(1);facts.offers['Q00A']=facts.offers['Q00A'].model_copy(update=change)
    d=qualify_purchase(snapshot.products[0],facts,'fixture-user',NOW)
    assert not d.purchase_eligible and reason in d.reason_codes

@pytest.mark.parametrize('status,reason',[('missing','PRODUCT_RISK_UNKNOWN'),('conflict','PRODUCT_RISK_CONFLICT')])
def test_unknown_conflict_risk_not_category_fallback(status,reason):
    snapshot,facts=fixture_pool(1,5);snapshot.products[0].fields['formal_risk']=FieldFact(status=status,reason='test-gap')
    d=qualify_purchase(snapshot.products[0],facts,'fixture-user',NOW)
    assert not d.purchase_eligible and d.product_level is None and reason in d.reason_codes

@pytest.mark.parametrize('change,reason',[
 ({'product_version':None},'PRODUCT_VERSION_UNKNOWN'),({'disclosure_version':None},'DISCLOSURE_VERSION_UNKNOWN'),
 ({'scope_status':'policy_mismatch'},'SIMULATED_POLICY_MISMATCH')])
def test_product_version_scope(change,reason):
    snapshot,facts=fixture_pool(1);product=snapshot.products[0].model_copy(update=change)
    assert reason in qualify_purchase(product,facts,'fixture-user',NOW).reason_codes

@pytest.mark.parametrize('variant,reason',[('unknown','PRODUCT_RISK_UNKNOWN'),('channel','PRODUCT_RISK_SHARE_CHANNEL_MISMATCH'),('time','PRODUCT_RISK_TIME_UNPROVEN'),('conflict','PRODUCT_RISK_CONFLICT')])
def test_real_risk_requires_exact_evidence(variant,reason):
    snapshot,facts=fixture_pool(1);p=snapshot.products[0];p.source_kind='real'
    from tests.fixtures.wealth_portfolio_candidates_v1 import evidence
    e=evidence('fixture-family','Q00A','Q00C',kind='risk').model_copy(update={'source_kind':'real','source_role':'seller'})
    p.risk_evidence=[] if variant=='unknown' else [e]
    if variant=='channel':p.risk_evidence=[e.model_copy(update={'channel':'different'})]
    if variant=='time':p.risk_evidence=[e.model_copy(update={'applicable_from':None,'applicable_to':None})]
    if variant=='conflict':p.risk_evidence.append(e.model_copy(update={'risk_level':4,'evidence_id':'second'}))
    d=qualify_purchase(p,facts,'fixture-user',NOW)
    assert not d.purchase_eligible and reason in d.reason_codes and d.product_level is None

@pytest.mark.parametrize('ratio',['0.00000001','0'])
def test_r2_r4_candidate_manual_small_and_zero(ratio):
    snapshot,facts=fixture_pool(1,2,4);engine,source=service(snapshot,facts)
    result=run(engine.candidates('fixture-user',GENERAL));assert result['status']=='risk_mismatch'
    assert result['pipeline']['stages']['matched']==2
    request=SelectionRequest(expected_context=version_context(facts),items=[share('Q00A',ratio)])
    d=run(engine.validate_selection('fixture-user',request))
    assert not d.accepted and d.failures['Q00A']==['RISK_EXCEEDS_CAPACITY']
    assert d.decisions[0].user_level==2 and d.decisions[0].product_level==4


def test_switch_and_invalid_mixed_selection_atomic():
    snapshot,facts=fixture_pool(3);risk(snapshot,facts,'Q00C',4);risk(snapshot,facts,'Q02A',4)
    engine,_=service(snapshot,facts);result=run(engine.candidates('fixture-user',GENERAL));s=result['session']
    selected=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q00A')])))
    s=selected['session'];before=deepcopy(s)
    switched=run(engine.switch_share('fixture-user',SwitchRequest(session_id=s['session_id'],expected_version=s['version'],family_id='fixture-family-00',target=share('Q00C','0'))))
    assert not switched['selection']['accepted'] and 'RISK_EXCEEDS_CAPACITY' in switched['selection']['failures']['Q00C']
    assert switched['session']==before
    invalid=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q01A'),share('Q02A','0.00000001')])))
    assert not invalid['selection']['accepted'] and invalid['session']==before
    duplicate=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q01A').model_copy(update={'product_version':2}),share('Q01A')])))
    assert not duplicate['selection']['accepted'] and duplicate['session']==before


def test_default_A_C_only_other_shares_and_duplicate_family():
    snapshot,facts=fixture_pool(3);facts.offers['Q00A'].purchase_allowed=False
    facts.offers.pop('Q01A');snapshot.products=[p for p in snapshot.products if p.product_id!='Q01A']
    for p in snapshot.products:
        if p.product_id.startswith('Q02'):
            new='B' if p.share_class=='A' else 'R';p.share_class=new
            for e in p.family_evidence:e.share_classes[p.product_id]=new
            facts.offers[p.product_id].share_class=new
    result=pipeline(ProductIndex(snapshot),GENERAL,facts,'fixture-user',NOW)
    assert {f.family_id:f.default_share.share_class for f in result.family_candidates}=={'fixture-family-00':'C','fixture-family-01':'C','fixture-family-02':'B'}
    engine,_=service(snapshot,facts)
    d=run(engine.validate_selection('fixture-user',SelectionRequest(expected_context=version_context(facts),items=[share('Q00C'),share('Q00A')])))
    assert not d.accepted and 'DUPLICATE_SELECTED_FAMILY' in d.reason_codes

@pytest.mark.parametrize('case,status,reason',[('none','no_theme_match','NO_STRUCTURED_MATCH'),('over','risk_mismatch','ALL_MATCHED_RISK_EXCEEDS_CAPACITY'),('mixed','no_eligible_products','PRODUCT_RISK_UNKNOWN'),('family','no_eligible_products','FAMILY_UNVERIFIED'),('text','ok',None),('assessment','unavailable','ASSESSMENT_REQUIRED')])
def test_complete_match_before_qualification_reasons(case,status,reason):
    snapshot,facts=fixture_pool(2)
    if case=='none':
        for p in snapshot.products:p.fields['strategy']=field('本基金配置普通债券。')
    if case=='over':
        for p in snapshot.products:risk(snapshot,facts,p.product_id,4)
    if case=='mixed':
        for p in snapshot.products:
            if p.product_id.endswith('A'):p.fields['formal_risk']=FieldFact(status='missing',reason='unknown')
            else:facts.offers[p.product_id].purchase_allowed=False
    if case=='family':
        for p in snapshot.products:p.family_evidence=[]
    if case=='text':
        # general仍匹配，用空文本检查排序资料阶段。
        for p in snapshot.products:p.fields['strategy']=FieldFact(status='missing',reason='no text')
    if case=='assessment':facts.assessment=None
    result=pipeline(ProductIndex(snapshot),GENERAL if case=='text' else THEME,facts,'fixture-user',NOW)
    assert result.status==status and (reason is None or reason in result.reason_codes)
    assert result.stages['matched']==(0 if case=='none' else 4)
    if case=='mixed':assert result.risk_explanation is None and 'PURCHASE_STOPPED' in result.reason_codes
    if case=='family':assert result.stages['purchase_qualified_shares']==4 and result.stages['family_groups']==0


def test_partial_good_has_no_group_risk_warning():
    snapshot,facts=fixture_pool(2)
    for code in ['Q00A','Q00C']:risk(snapshot,facts,code,4)
    result=pipeline(ProductIndex(snapshot),THEME,facts,'fixture-user',NOW)
    assert result.status=='ok' and result.risk_explanation is None and len(result.family_candidates)==1
    assert len(result.matched_codes)==4 and 'RISK_EXCEEDS_CAPACITY' in result.exclusions['Q00A']

@pytest.mark.parametrize('text',['本基金不投资于高股息股票。','与其他基金的高股息策略相比，本基金采用普通债券。','例如其他基金重点投资于高股息资产。','高股息是某类基金的策略。'])
def test_negation_and_mentions_no_theme(text):
    snapshot,_=fixture_pool(1);p=snapshot.products[0];p.fields['strategy']=field(text)
    assert not theme_tags(p)


def test_name_dividend_not_tags_and_exact_verified_mapping():
    snapshot,_=fixture_pool(1);p=snapshot.products[0];p.fields['strategy']=field('普通债券策略')
    p.name='高股息红利';p.fields['dividend_count']=field('100')
    assert not theme_tags(p)
    p.fields['tracking_target']=field('核实宽基标的','tracking_target')
    mapping={'核实宽基标的':{'verified':True,'source_kind':'fixture','tags':['broad_market'],'source':'fixture_only','date':'2026-10-01','sha256':'b'*64}}
    assert [t.tag for t in theme_tags(p,mapping)]==['broad_market']
    mapping['核实宽基标的']['source_kind']='simulated';assert not theme_tags(p,mapping)
    assert not theme_tags(p,{'相似宽基标的':mapping['核实宽基标的']})


def test_AND_OR_and_invalid_structured_filters():
    snapshot,facts=fixture_pool(2);snapshot.products[0].fields['strategy']=field('本基金主要投资于高股息股票。')
    index=ProductIndex(snapshot)
    a=index.match(CandidateFilter(mode='theme',theme_keys=['high_dividend','bond_income'],theme_operator='AND'))
    b=index.match(CandidateFilter(mode='theme',theme_keys=['high_dividend','bond_income'],theme_operator='OR'))
    assert len(a)==3 and len(b)==4
    for value in [{'mode':'theme','theme_key':''},{'mode':'theme','theme_keys':['high_dividend']},{'mode':'category','category':None}]:
        with pytest.raises(ValueError):CandidateFilter.model_validate(value)
    with pytest.raises(ValueError,match='UNKNOWN_THEME'):index.match(CandidateFilter(mode='theme',theme_key='undefined'))


def test_single_issuer_original_sufficient_and_conflicts_not_merged():
    snapshot,facts=fixture_pool(1);p=snapshot.products[0]
    assert issuer_identity(p)==('虚构基金公司甲',None) and 'issuer_normalized' not in p.fields
    snapshot.products[1].fields['issuer_raw']=field('虚构公司甲')
    result=pipeline(ProductIndex(snapshot),GENERAL,facts,'fixture-user',NOW)
    assert result.status=='ok' and 'FAMILY_ISSUER_CONFLICT' not in result.reason_codes
    p.fields['issuer_raw']=FieldFact(status='conflict',reason='short and full names conflict')
    assert issuer_identity(p)[0] is None


def test_family_unknown_not_product_id_and_text_conflict():
    snapshot,facts=fixture_pool(1)
    for p in snapshot.products:p.family_evidence=[]
    result=pipeline(ProductIndex(snapshot),GENERAL,facts,'fixture-user',NOW)
    assert not result.family_candidates and 'FAMILY_UNVERIFIED' in result.reason_codes
    snapshot,facts=fixture_pool(1);snapshot.products[1].fields['strategy']=field('本基金主要投资于高股息股票。')
    result=pipeline(ProductIndex(snapshot),GENERAL,facts,'fixture-user',NOW)
    assert result.status=='ok' and 'FAMILY_TEXT_CONFLICT' not in result.reason_codes


def test_full_pool_batches_selected_more_than_three_reused_index():
    engine,source=service();r=run(engine.candidates('fixture-user',GENERAL));assert r['pipeline']['stages']['material_complete_families']==8
    seen={i['family_id'] for i in r['items']};assert len(seen)==3;s=r['session']
    # 手动选择包括未展示产品，共4家族，不能限制为每批3或旧5笔。
    selected=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q00A'),share('Q01A'),share('Q02A'),share('Q07A')])))
    assert selected['selection']['accepted'] and len(selected['session']['selected'])==4;s=selected['session']
    next_result=run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert not (seen & {i['family_id'] for i in next_result['items']}) and len(next_result['session']['selected'])==4
    s=next_result['session'];last=run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert len(last['items'])==1 and last['session']['cursor'] is None
    s=last['session'];exhausted=run(engine.refresh('fixture-user',SessionAction(session_id=s['session_id'],expected_version=s['version'])))
    assert all(i['family_id'] not in {'fixture-family-00','fixture-family-01','fixture-family-02','fixture-family-07'} for i in exhausted['items'])
    assert engine.indexes.loads==1 and source.calls>=4

@pytest.mark.parametrize('variant',['assessment','daily','data','offer'])
def test_old_cursor_facts_versions_rejected_and_selection_preserved(variant):
    engine,source=service();r=run(engine.candidates('fixture-user',GENERAL));s=r['session']
    r=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q00A')])));s=r['session']
    if variant=='assessment':source.facts.assessment.version=2;source.facts.latest_assessment_version=2
    if variant=='offer':source.facts.offers['Q00A'].purchase_allowed=False
    if variant=='daily':source.facts.daily_version='new-daily'
    if variant=='data':source.facts.data_version='new-data'
    with pytest.raises(CandidateError) as error:run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert error.value.code in ('CANDIDATE_CONTEXT_STALE','DAILY_VERSION_MISMATCH','DATA_VERSION_MISMATCH')
    assert engine.sessions[s['session_id']].selected['Q00A']['reference']==s['selected']['Q00A']['reference']
    if variant=='offer':assert engine.sessions[s['session_id']].selected['Q00A']['valid'] is False


def test_stale_product_card_and_unconfigured_selector_source():
    snapshot,facts=fixture_pool(1);engine,_=service(snapshot,facts,selector=None)
    r=run(engine.candidates('fixture-user',GENERAL));assert r['status']=='unavailable' and r['reason_codes']==['SELECTOR_UNAVAILABLE']
    request=SelectionRequest(expected_context=version_context(facts),items=[share('Q00A').model_copy(update={'rules_version':'old'})])
    d=run(engine.validate_selection('fixture-user',request));assert not d.accepted and 'SELECTED_RULES_VERSION_MISMATCH' in d.reason_codes
    unavailable=CandidateService(None,SnapshotIndexCache(lambda _:ProductIndex(snapshot)))
    with pytest.raises(CandidateError) as error:run(unavailable.candidates('fixture-user',GENERAL))
    assert error.value.status_code==503 and error.value.code=='ASSESSMENT_SOURCE_UNAVAILABLE'


def test_fixture_source_cannot_be_enabled_by_client_or_normal_service():
    snapshot,facts=fixture_pool(1);engine,_=service(snapshot,facts,enable_fixture=False)
    with pytest.raises(CandidateError,match='FIXTURE_SOURCE_FORBIDDEN'):run(engine.candidates('fixture-user',GENERAL))
    with pytest.raises(ValueError):CandidateFilter.model_validate({'mode':'general','fixture':True,'risk_level':5})

def test_selection_six_families_and_explicit_exhausted():
    snapshot,facts=fixture_pool(9);engine,_=service(snapshot,facts)
    result=run(engine.candidates('fixture-user',GENERAL));s=result['session']
    chosen=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share(f'Q{i:02d}A') for i in [0,1,2,3,4,8]])))
    assert chosen['selection']['accepted'] and len(chosen['session']['selected'])==6;s=chosen['session']
    nxt=run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert len(nxt['items'])==3 and nxt['pool_exhausted'];s=nxt['session']
    exhausted=run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor='old-cursor')))
    assert exhausted['reason_codes']==['POOL_EXHAUSTED'] and exhausted['items']==[] and len(exhausted['session']['selected'])==6


def test_new_data_index_old_selection_invalid_not_auto_switch():
    snapshot,facts=fixture_pool(8);engine,source=service(snapshot,facts)
    r=run(engine.candidates('fixture-user',GENERAL));s=r['session']
    r=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q00A')])));s=r['session']
    newer=deepcopy(snapshot);newer.version='fixture-data-2';risk(newer,source.facts,'Q00A',4);source.facts.data_version=newer.version
    engine.indexes.loader=lambda version:ProductIndex(newer if version==newer.version else snapshot)
    with pytest.raises(CandidateError,match='CANDIDATE_CONTEXT_STALE'):
        run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    state=engine.sessions[s['session_id']]
    assert 'Q00A' in state.selected and 'Q00C' not in state.selected and not state.selected['Q00A']['valid'] and engine.indexes.loads==2
    refreshed=run(engine.refresh('fixture-user',SessionAction(session_id=s['session_id'],expected_version=s['version'])))
    assert not refreshed['session']['selected']['Q00A']['valid'] and 'RISK_EXCEEDS_CAPACITY' in refreshed['session']['selected']['Q00A']['reason_codes']


def test_expired_assessment_and_unknown_fact_source_invalidate_saved_selection():
    engine,source=service();r=run(engine.candidates('fixture-user',GENERAL));s=r['session']
    r=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share('Q00A')])));s=r['session']
    engine.clock=lambda:source.facts.assessment.expires_at
    with pytest.raises(CandidateError,match='CANDIDATE_CONTEXT_STALE'):run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert not engine.sessions[s['session_id']].selected['Q00A']['valid']


