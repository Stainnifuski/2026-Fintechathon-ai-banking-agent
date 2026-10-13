import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import pytest
from backend.app.schemas.wealth_portfolio import PortfolioSelection,SelectionItem
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate,PortfolioRecommendation,SelectedShare,CandidateBinding
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter,SessionSelectionRequest
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.domain.wealth_portfolio_allocation import ModelError
from backend.app.services.orchestration.wealth_portfolio_data import digest
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import field,share

U='fixture-user';C='fixture-context'
def run(c):return asyncio.run(c)
def create(s,p=None,selected=None):return s.create(U,PortfolioCreate(context_id=C,growth_ratio_ex_cash=p,selected=selected))
def edit(p,**kw):
    value=dict(portfolio_id=p.portfolio_id,expected_version=p.version,snapshot_id=p.snapshot_id,context_id=C,items=p.selected_positions)
    value.update(kw);return PortfolioSelection(**value)
def state(s,p):return digest(s.store.rows[p.portfolio_id][0].__dict__ | {'plan':s.store.rows[p.portfolio_id][0].plan.model_dump(mode='json'),'explicit':[r.model_dump(mode='json') for r in s.store.rows[p.portfolio_id][0].explicit]})
def identity(row):return SelectedShare(product_id=row.product_id,product_version=row.product_version,share_class=row.share_class)
def all_items(p):return [SelectionItem(product_id=r.product_id,product_version=r.product_version,share_class=r.share_class,target_ratio_total=str(Decimal(r.target_fen)/p.basis_fen)) for r in p.advised_positions]

@pytest.mark.asyncio
async def test_create_read_adjust_preference_copies_original_U():
    s,*_=lifecycle_fixture();p=await create(s,'.625');assert p.version==1 and p.growth_ratio_ex_cash=='0.625' and p.selected_positions==[]
    before=deepcopy(s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation'])
    read=await s.get(U,p.portfolio_id,C);assert read==p
    read.model_view.initial_ratios[0]='fake';assert (await s.get(U,p.portfolio_id,C)).model_view.initial_ratios[0]!='fake'
    p=await s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.626'))
    p=await s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.627'))
    assert p.version==3 and s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation']==before
    p=await s.revise(U,p.portfolio_id,edit(p,revision_kind='preference',growth_ratio_ex_cash='.65'))
    assert p.version==4 and p.growth_ratio_ex_cash=='0.65'
    assert s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation']['b']!=before['b']

@pytest.mark.asyncio
async def test_concurrent_same_version_controlled_CAS():
    s,*_=lifecycle_fixture();p=await create(s,'.625');arrived=0;barrier=asyncio.Event();replace=s.store.replace
    async def controlled(record,version):
        nonlocal arrived
        arrived+=1
        if arrived==2:barrier.set()
        await asyncio.wait_for(barrier.wait(),3);await replace(record,version)
    s.store.replace=controlled
    result=await asyncio.gather(s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.626')),s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.627')),return_exceptions=True)
    assert sum(not isinstance(r,Exception) for r in result)==1
    assert [r.code for r in result if isinstance(r,CandidateError)]==['PORTFOLIO_VERSION_STALE']
    assert (await s.get(U,p.portfolio_id,C)).version==2

@pytest.mark.asyncio
async def test_manual_100_fen_preserved_and_omitted_holdings_not_zero():
    s,*_=lifecycle_fixture();p=await create(s,'.625');items=all_items(p);a=next(r for r in items if r.product_id=='Q02A');b=next(r for r in items if r.product_id=='Q06A')
    a.target_ratio_total=str(Decimal(a.target_ratio_total)+Decimal('0.0001'));b.target_ratio_total=str(Decimal(b.target_ratio_total)-Decimal('0.0001'))
    p2=await s.revise(U,p.portfolio_id,edit(p,items=items,cash_residual_fen=p.cash_residual_fen))
    assert next(r for r in p2.advised_positions if r.product_id=='Q02A').target_fen==next(r for r in p.advised_positions if r.product_id=='Q02A').target_fen+100
    assert sum(r.target_fen for r in p2.advised_positions)+p2.cash_residual_fen==p2.basis_fen
    # 未在完整新购集合重写的原持仓仍保留服务端目标。
    items=[r for r in p2.selected_positions if r.product_id not in {'Q01A','Q03A'}]
    p3=await s.revise(U,p.portfolio_id,edit(p2,items=items))
    assert {r.product_id:r.target_fen for r in p3.advised_positions if r.current_fen}=={r.product_id:r.target_fen for r in p2.advised_positions if r.current_fen}

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['old_version','snapshot','context','path','precision','overallocate','cash','preference','compound_U','growth_mismatch'])
async def test_rejected_revision_keeps_whole_state(kind):
    s,*_=lifecycle_fixture();p=await create(s,'.625');before=state(s,p);kw={};path=p.portfolio_id
    if kind=='old_version':kw={'expected_version':2}
    elif kind=='snapshot':kw={'snapshot_id':'old'}
    elif kind=='context':kw={'context_id':'other'}
    elif kind=='path':path='other'
    elif kind=='precision':kw={'items':[SelectionItem(product_id='Q02A',product_version=1,share_class='A',target_ratio_total='.0000001')]}
    elif kind=='overallocate':kw={'items':[SelectionItem(product_id='Q02A',product_version=1,share_class='A',target_ratio_total='1')]}
    elif kind=='cash':kw={'items':all_items(p),'cash_residual_fen':p.cash_residual_fen+1}
    elif kind=='preference':kw={'revision_kind':'preference','growth_ratio_ex_cash':'.76'}
    elif kind=='growth_mismatch':kw={'items':all_items(p),'growth_ratio_ex_cash':'.5','cash_residual_fen':p.cash_residual_fen}
    else:
        # 两个边际均合法，但完整f=.013333 > .01。
        items=all_items(p);shift=Decimal('0.073029');cash=next(r for r in items if r.product_id=='Q00A');growth=next(r for r in items if r.product_id=='Q03A')
        cash.target_ratio_total=str(Decimal(cash.target_ratio_total)+shift);growth.target_ratio_total=str(Decimal(growth.target_ratio_total)-shift)
        kw={'items':items,'cash_residual_fen':p.cash_residual_fen}
    with pytest.raises((CandidateError,ModelError)):await s.revise(U,path,edit(p,**kw))
    assert state(s,p)==before

@pytest.mark.asyncio
@pytest.mark.parametrize('ratio,shareclass',[('0','A'),('.000001','A'),('0','C'),('.000001','C')])
async def test_R2_R4_zero_tiny_and_AC_cannot_bypass(ratio,shareclass):
    s,data,facts,*_=lifecycle_fixture(2)
    code='Q07'+shareclass;next(r for r in data.products if r.product_id==code).fields['formal_risk']=field(4,'risk_level');facts.offers[code].risk_level=4
    p=await create(s);before=state(s,p)
    with pytest.raises(CandidateError,match='PORTFOLIO_SELECTION_REJECTED') as e:
        await s.revise(U,p.portfolio_id,edit(p,items=[SelectionItem(product_id=code,product_version=1,share_class=shareclass,target_ratio_total=ratio)]))
    assert 'RISK_EXCEEDS_CAPACITY' in e.value.details['reason_codes'] and state(s,p)==before

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['cash','assessment','offer','product','epoch','scope','session','expired','offer_expired','control'])
async def test_current_facts_change_or_expiry_refuses_old_draft(change):
    s,data,facts,bundle,clock=lifecycle_fixture();p=await create(s);before=state(s,p)
    if change=='cash':s.provider.asset_source.bundle['summary']['snapshot_id']='changed'
    elif change=='assessment':facts.assessment.version=2
    elif change=='offer':facts.offers['Q00A'].purchase_allowed=False
    elif change=='product':data.products[0].fields['strategy']=field('变更投资策略')
    elif change=='epoch':s.access.grant['context_epoch']=2
    elif change=='scope':s.access.grant['account_ids'].append('not-linked')
    elif change=='session':s.access.grant['session_ref']='new-session'
    elif change=='expired':facts.assessment.expires_at=clock[0]
    elif change=='offer_expired':facts.offers['Q00A'].valid_until=clock[0]
    else:s.access.grant['can_edit']=False
    with pytest.raises((CandidateError,ModelError)):await s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.526'))
    assert state(s,p)==before
    if change!='control':
        with pytest.raises((CandidateError,ModelError)):await s.get(U,p.portfolio_id,C)

@pytest.mark.asyncio
async def test_read_at_advance_valid_without_extending_expiry_and_idle_cleanup():
    s,_,_,_,clock=lifecycle_fixture();p=await create(s)
    clock[0]+=timedelta(minutes=5);assert (await s.get(U,p.portfolio_id,C)).version==1
    b=s.provider.asset_source.bundle;b['summary']['as_of']=(clock[0]).isoformat();b['summary']['business_at']=clock[0].isoformat();b['assessment_provenance']['read_at']=clock[0].isoformat()
    assert (await s.get(U,p.portfolio_id,C)).version==1
    p=await s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.526'));assert p.version==2
    clock[0]+=timedelta(minutes=30)
    with pytest.raises(CandidateError,match='PORTFOLIO_NOT_FOUND'):await s.get(U,p.portfolio_id,C)

@pytest.mark.asyncio
async def test_other_user_private404_and_restart():
    s,*_=lifecycle_fixture();p=await create(s)
    for pid in [p.portfolio_id,'does-not-exist']:
        with pytest.raises(CandidateError) as e:await s.get('other',pid,C)
        assert e.value.status_code==404 and e.value.code=='PORTFOLIO_NOT_FOUND'
    s.store.rows.clear()
    with pytest.raises(CandidateError,match='PORTFOLIO_NOT_FOUND'):await s.get(U,p.portfolio_id,C)

@pytest.mark.asyncio
async def test_independent_recommend_selected_six_binding_and_missing_assets_preserves():
    s,*_=lifecycle_fixture();refs=[SelectedShare(product_id=f'Q{i:02}A',product_version=1,share_class='A') for i in range(6)]
    group=await s.recommend(U,PortfolioRecommendation(context_id=C,filter=CandidateFilter(mode='general'),selected=refs));assert group.status=='ok'
    binding=CandidateBinding(session_id=group.session_binding['session_id'],expected_version=group.session_binding['version'],filter=CandidateFilter(mode='general'))
    before=deepcopy(s.provider.candidates.sessions[binding.session_id])
    p=await s.create(U,PortfolioCreate(context_id=C,selected=refs,candidate_binding=binding));assert len(p.selected_positions)==6
    assert s.provider.candidates.sessions[binding.session_id]==before
    assets=s.provider.asset_source;s.provider.asset_source=None
    with pytest.raises(Exception):await s.create(U,PortfolioCreate(context_id=C,selected=refs,candidate_binding=binding))
    assert s.provider.candidates.sessions[binding.session_id]==before
    # 独立推荐没有资产前置。
    assert (await s.recommend(U,PortfolioRecommendation(context_id=C,filter=CandidateFilter(mode='category',category='equity_fund')))).status in ('ok','no_category_match')
    s.provider.asset_source=assets

@pytest.mark.asyncio
async def test_hold_only_exact_share_cap_no_family_AC_increase():
    s,data,facts,*_=lifecycle_fixture(2)
    for code in ['Q03A','Q03C']:
        next(r for r in data.products if r.product_id==code).fields['formal_risk']=field(4,'risk_level');facts.offers[code].risk_level=4
    p=await create(s);rows=all_items(p);old=next(r for r in rows if r.product_id=='Q03A');before=state(s,p)
    old.target_ratio_total='.500001'
    with pytest.raises(CandidateError,match='PORTFOLIO_HOLDING_CAP_EXCEEDED'):await s.revise(U,p.portfolio_id,edit(p,items=[old]))
    assert state(s,p)==before
    old.target_ratio_total='.1';rows.append(SelectionItem(product_id='Q03C',product_version=1,share_class='C',target_ratio_total='0'))
    with pytest.raises(CandidateError,match='PORTFOLIO_SELECTION_REJECTED'):await s.revise(U,p.portfolio_id,edit(p,items=rows))
    assert state(s,p)==before

@pytest.mark.asyncio
async def test_actual_source_missing_never_fixture_fallback():
    s,*_=lifecycle_fixture();s.provider.candidates.source=None
    with pytest.raises(CandidateError) as e:await create(s)
    assert e.value.status_code==503 and not s.store.rows


@pytest.mark.asyncio
async def test_facts_change_during_compute_rejected_before_store_replace():
    s,*_=lifecycle_fixture();p=await create(s,'.625');before=state(s,p);original=s._manual
    def changing(*args):
        result=original(*args);s.provider.candidates.source.facts.offers['Q00A'].purchase_allowed=False;return result
    s._manual=changing
    with pytest.raises(CandidateError,match='PORTFOLIO_FACTS_STALE'):await s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.626'))
    assert state(s,p)==before

@pytest.mark.asyncio
async def test_preference_uses_only_item_identity_not_previous_ratio():
    s,*_=lifecycle_fixture();p=await create(s,'.625');rows=all_items(p)
    for r in rows:r.target_ratio_total='0'
    changed=await s.revise(U,p.portfolio_id,edit(p,revision_kind='preference',growth_ratio_ex_cash='.65',items=rows))
    assert changed.growth_ratio_ex_cash=='0.65' and any(Decimal(r.target_ratio_total)>0 for r in changed.selected_positions)

@pytest.mark.asyncio
async def test_unchanged_ratio_roundtrip_nonterminating_decimal_exact_fen():
    s,*_=lifecycle_fixture(holding=False);b=s.provider.asset_source.bundle
    b['summary'].update(cash_balance_fen=999983,available_cash_fen=999983,total_assets_fen=999983)
    b['accounts'][0].update(balance_fen=999983,available_fen=999983)
    p=await create(s);rows=all_items(p)
    next_plan=await s.revise(U,p.portfolio_id,edit(p,items=rows,cash_residual_fen=p.cash_residual_fen))
    assert {r.product_id:r.target_fen for r in p.advised_positions}=={r.product_id:r.target_fen for r in next_plan.advised_positions}

@pytest.mark.asyncio
async def test_R1_no_slider_and_F18_total_available_receivable_held():
    s,*_=lifecycle_fixture(1);p=await create(s);assert p.growth_ratio_ex_cash is None and p.model_view.slider_bounds is None
    from scripts.wealth_portfolio_dev import services
    f=services['F18'];p=await f.create(U,PortfolioCreate(context_id='fixture-context-F18'))
    assert (p.basis_fen,p.model_view.available_cash_fen,p.model_view.receivable_fen,p.model_view.held_fen)==(1000000,700000,50000,100000)
    assert sum(r.target_fen for r in p.advised_positions)+p.cash_residual_fen==p.basis_fen


@pytest.mark.asyncio
async def test_recommend_next_batch_keeps_selection_and_context_binding():
    s,*_=lifecycle_fixture();refs=[SelectedShare(product_id='Q00A',product_version=1,share_class='A')];filter=CandidateFilter(mode='general')
    group=await s.recommend(U,PortfolioRecommendation(context_id=C,filter=filter,selected=refs));session=group.session_binding
    assert not any(r.family_id=='fixture-family-00' for r in group.items)
    binding=CandidateBinding(session_id=session['session_id'],expected_version=session['version'],filter=filter)
    group2=await s.recommend(U,PortfolioRecommendation(context_id=C,filter=filter,cursor=session['cursor'],candidate_binding=binding))
    assert group2.session_binding['selected']==session['selected'] and group2.session_binding['version']==session['version']+1
    assert not {r.family_id for r in group.items}&{r.family_id for r in group2.items}
    binding.expected_version=group2.session_binding['version'];before=deepcopy(s.provider.candidates.sessions[binding.session_id])
    s.access.grant['context_epoch']=2;s.provider.asset_source.bundle['context_epoch']=2
    with pytest.raises(CandidateError,match='PORTFOLIO_CANDIDATE_BINDING_STALE'):await s.create(U,PortfolioCreate(context_id=C,selected=refs,candidate_binding=binding))
    assert s.provider.candidates.sessions[binding.session_id]==before

@pytest.mark.asyncio
async def test_preference_missing_items_preserves_explicit_identities():
    s,*_=lifecycle_fixture();p=await create(s);items=all_items(p);p=await s.revise(U,p.portfolio_id,edit(p,items=items,cash_residual_fen=p.cash_residual_fen))
    original={r.product_id for r in p.selected_positions}
    p2=await s.revise(U,p.portfolio_id,edit(p,revision_kind='preference',items=[],growth_ratio_ex_cash='.65'))
    assert {r.product_id for r in p2.selected_positions}==original

@pytest.mark.asyncio
async def test_existing_access_adapter_reuses_plan_and_context_write_gate():
    from backend.app.services.orchestration.wealth_portfolio_access import ExistingPortfolioAccess
    from backend.app.services.orchestration import wealth_task
    from unittest.mock import AsyncMock,patch
    from types import SimpleNamespace
    s,*_=lifecycle_fixture()
    async def scope(u,c):return deepcopy(s.access.grant)
    class Rows:
        def all(self):return [SimpleNamespace(control_mode='HUMAN',status='WAITING',data={})]
    db=SimpleNamespace(scalars=AsyncMock(return_value=Rows()))
    adapter=ExistingPortfolioAccess(db,scope)
    with patch.object(wealth_task,'require_plan_edit',new=AsyncMock()) as guard:
        with pytest.raises(CandidateError,match='TASK_WRITE_BLOCKED'):await adapter.resolve(U,C,write=True,portfolio_id='example')
        guard.assert_awaited_once_with(db,U,'example')
    assert (await adapter.resolve(U,C,write=False))['user_id']==U
    assert not hasattr(db,'commit')


@pytest.mark.asyncio
async def test_lifecycle_has_no_network_or_asset_order_mutations(monkeypatch):
    import socket
    def forbidden(*args,**kwargs):raise AssertionError('external socket connection forbidden')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    s,*_=lifecycle_fixture();original=deepcopy(s.provider.asset_source.bundle)
    p=await create(s,'.625');p=await s.revise(U,p.portfolio_id,edit(p,growth_ratio_ex_cash='.626'))
    p=await s.revise(U,p.portfolio_id,edit(p,revision_kind='preference',growth_ratio_ex_cash='.65'))
    assert (await s.get(U,p.portfolio_id,C)).version==3
    assert s.provider.asset_source.bundle==original

@pytest.mark.asyncio
async def test_existing_plan_write_error_adapted_to_business_error():
    from backend.app.services.orchestration.wealth_portfolio_access import ExistingPortfolioAccess
    from backend.app.services.orchestration import wealth_task
    from backend.app.integrations.wealth_authorization import WealthPolicyError
    from unittest.mock import AsyncMock,patch
    s,*_=lifecycle_fixture()
    async def scope(u,c):return deepcopy(s.access.grant)
    with patch.object(wealth_task,'require_plan_edit',new=AsyncMock(side_effect=WealthPolicyError('人工接管','TASK_WRITE_BLOCKED'))):
        with pytest.raises(CandidateError) as e:await ExistingPortfolioAccess(object(),scope).resolve(U,C,write=True,portfolio_id='example')
        assert e.value.code=='TASK_WRITE_BLOCKED' and e.value.status_code==409


@pytest.mark.asyncio
async def test_original_zero_holding_is_reduction_not_new_purchase_and_store_metadata_copy():
    s,data,facts,*_=lifecycle_fixture(1)
    next(r for r in data.products if r.product_id=='Q03A').fields['formal_risk']=field(4,'risk_level');facts.offers['Q03A'].risk_level=4
    p=await create(s);original=state(s,p)
    copied=await s.store.read(p.portfolio_id,U);copied.metadata['validation_contract']['calculation']['delta']=99
    assert state(s,p)==original
    items=all_items(p);assert next(r for r in items if r.product_id=='Q03A').target_ratio_total=='0'
    p2=await s.revise(U,p.portfolio_id,edit(p,items=items,cash_residual_fen=p.cash_residual_fen))
    assert p2.version==2 and next(r for r in p2.advised_positions if r.product_id=='Q03A').target_fen==0
    assert 'Q03A' not in {r.product_id for r in p2.selected_positions}


@pytest.mark.asyncio
@pytest.mark.parametrize('expiry',['assessment','offer'])
async def test_time_passage_alone_does_not_extend_evidence_expiry(expiry):
    s,_,facts,_,clock=lifecycle_fixture()
    until=clock[0]+timedelta(minutes=5)
    if expiry=='assessment':
        facts.assessment.expires_at=until;s.provider.asset_source.bundle['assessment']['expires_at']=until.isoformat()
    else:facts.offers['Q00A'].valid_until=until
    p=await create(s);before=state(s,p);clock[0]+=timedelta(minutes=6)
    with pytest.raises(CandidateError) as e:await s.get(U,p.portfolio_id,C)
    assert e.value.status_code==409 and state(s,p)==before


@pytest.mark.asyncio
async def test_candidate_binding_validated_through_atomic_store_insert():
    s,*_=lifecycle_fixture();refs=[SelectedShare(product_id=f'Q{i:02}A',product_version=1,share_class='A') for i in range(6)];filter=CandidateFilter(mode='general')
    group=await s.recommend(U,PortfolioRecommendation(context_id=C,filter=filter,selected=refs));session=group.session_binding
    binding=CandidateBinding(session_id=session['session_id'],expected_version=session['version'],filter=filter)
    arrived=asyncio.Event();release=asyncio.Event();insert=s.store.insert
    async def controlled(record):
        assert s.provider.candidates.lock.locked();arrived.set();await release.wait();await insert(record)
    s.store.insert=controlled
    creation=asyncio.create_task(s.create(U,PortfolioCreate(context_id=C,selected=refs,candidate_binding=binding)))
    await asyncio.wait_for(arrived.wait(),3)
    selection=asyncio.create_task(s.provider.candidates.select(U,SessionSelectionRequest(session_id=binding.session_id,expected_version=1,items=[share('Q06A')])))
    await asyncio.sleep(0);assert not selection.done();release.set()
    p=await creation;result=await selection
    assert len(p.selected_positions)==6 and result['selection']['accepted'] and result['session']['version']==2
    assert s.store.rows[p.portfolio_id][0].metadata['candidate_binding']['expected_version']==1
