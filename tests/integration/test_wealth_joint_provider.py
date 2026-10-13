"""Real bank facts + canonical A wiring; recorded plans never prove positive integration."""
from pathlib import Path
from copy import deepcopy
from pathlib import Path
import pytest
from sqlalchemy import select,func,update,delete
from backend.app.models import AccountORM,TransactionORM
from backend.app.models.wealth import WealthProfileORM,WealthOrderORM,WealthCashHoldORM,WealthProductVersionORM,WealthNavORM
from backend.app.schemas.wealth import AssessmentSubmission
from backend.app.domain.wealth_risk import QUESTIONS,QUESTIONNAIRE_VERSION
from backend.app.integrations.wealth_authorization import submit_assessment,confirm_assessment
from backend.app.services.orchestration.wealth_portfolio_joint import JointPortfolio,JointAccess
from backend.app.services.orchestration.wealth_portfolio import build_snapshot
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioRecommendation
from tests.integration.test_wealth_bank import store,http_store,assess
from tests.integration.test_wealth_asset_summary import seed_f18
from tests.integration.test_wealth_portfolio_prepare import setup

@pytest.mark.asyncio
async def test_joint_snapshot_is_exact_b_summary_and_explicit_lot_version(store,http_store):
    await seed_f18(store)
    _,bank,_=http_store
    provider=JointPortfolio(Path(__file__).resolve().parents[2],bank_factory=lambda:bank)
    async with store() as db:
        await assess(db)
        facts,_=provider.bind(db,'U1',bank)
        raw=await bank.assets('U1');snap=build_snapshot(await facts.read('U1'))
        assert snap.asset_scope_id==raw['asset_summary']['asset_scope_id']
        assert snap.snapshot_id==raw['asset_summary']['snapshot_id']
        assert snap.account_ids==['A1','A3'] and snap.total_assets_fen==1_000_000
        assert snap.positions[0].product_version==1 and snap.receivable_fen==50_000
        grant=await JointAccess(facts).resolve('U1','legacy-v1',write=True)
        assert grant['can_edit'] is True and grant['context_epoch'] is None

@pytest.mark.asyncio
async def test_joint_current_profile_proof_changes_on_actual_profile_revision(store,http_store):
    _,bank,_=http_store
    provider=JointPortfolio(Path(__file__).resolve().parents[2],bank_factory=lambda:bank)
    async with store() as db:
        await assess(db);facts,_=provider.bind(db,'U1',bank)
        from datetime import datetime,timezone
        assert (await facts.read_current('U1',datetime.now(timezone.utc))).assessment_current_proven
        profile=await db.get(WealthProfileORM,'U1');profile.version+=1;await db.commit()
        assert not (await facts.read_current('U1',datetime.now(timezone.utc))).assessment_current_proven

@pytest.mark.asyncio
@pytest.mark.parametrize('weight',['0','0.000001','0.1'])
async def test_final_prepare_r2_rejects_actual_r4_even_zero_or_tiny_before_order_creation(store,http_store,monkeypatch,weight):
    # Deliberately adverse target supplied by a test-only provider. This is a
    # negative final-boundary test, never counted as A positive recommendation.
    client,bank,source,_=await setup(store,http_store,monkeypatch)
    async with store() as db:
        row=await submit_assessment(db,'U1',AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,
            profile_version=1,adult=True,full_capacity=True,answers={k:1 for k,_,_ in QUESTIONS}))
        await confirm_assessment(db,'U1',row['assessment_id'])
        assert row['capacity_level']==2
    plan=await source.record(bank,[('MF004','growth',weight)])
    response=await client.post('/api/v1/wealth/portfolio/plans/'+plan['portfolio_id']+'/prepare',json={'expected_version':plan['version'],'snapshot_id':plan['snapshot_id']})
    assert response.status_code==409 and response.json()['detail']['code']=='SUITABILITY_MISMATCH',response.text
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        assert await db.scalar(select(func.count()).select_from(TransactionORM))==0
        assert (await db.get(AccountORM,'A1')).balance_fen==2_000_000

@pytest.mark.asyncio
async def test_a05_missing_valuation_does_not_require_a01_plan(store,http_store,monkeypatch):
    await seed_f18(store)
    _,bank,_=http_store
    provider=JointPortfolio(Path(__file__).resolve().parents[2],bank_factory=lambda:bank)
    from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool
    from backend.app.domain.wealth_portfolio_candidates import ProductIndex
    from backend.app.services.orchestration.wealth_portfolio_candidates import SnapshotIndexCache
    data,_=fixture_pool();provider.data_version=data.version;provider.daily_version=data.daily_version
    provider.indexes=SnapshotIndexCache(lambda version:ProductIndex(data))
    async with store() as db:
        await assess(db)
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id=='MF002:1'));await db.commit()
        facts,service=provider.bind(db,'U1',bank)
        async def forbidden(user):pytest.fail('A05 must not build A01 assets/plan')
        monkeypatch.setattr(facts,'read',forbidden)
        result=await service.recommend('U1',PortfolioRecommendation(context_id='legacy-v1',filter={'mode':'general'}))
        assert 'VALUATION_INCOMPLETE' not in result.reason_codes
        assert not provider.store.rows
        # This is a dependency-boundary assertion, not a positive product recommendation.

@pytest.mark.asyncio
async def test_related_real_session_close_invalidates_old_plan_access(store,http_store):
    from backend.app.models.wealth import WealthConversationORM
    from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
    from types import SimpleNamespace
    _,bank,_=http_store
    provider=JointPortfolio(Path(__file__).resolve().parents[2],bank_factory=lambda:bank)
    class OwnedRecord:
        async def read(self,key,user):return SimpleNamespace(access={'session_ref':'joint-owned-session'})
    async with store() as db:
        db.add(WealthConversationORM(session_id='joint-owned-session',user_id='U1',version=1,data={'context_id':'legacy-v1','closed':False}));await db.commit()
        facts,_=provider.bind(db,'U1',bank);access=JointAccess(facts,OwnedRecord())
        assert (await access.resolve('U1','legacy-v1',write=False,portfolio_id='owned'))['session_ref']=='joint-owned-session'
        row=await db.get(WealthConversationORM,'joint-owned-session');row.data={**row.data,'closed':True};await db.commit()
        with pytest.raises(CandidateError,match='PORTFOLIO_SESSION_CLOSED'):
            await access.resolve('U1','legacy-v1',write=False,portfolio_id='owned')

def test_main_registers_one_canonical_snapshot_and_real_provider():
    from backend.app.main import app
    from backend.app.routers import wealth,wealth_portfolio
    paths=[r for r in wealth_portfolio.router.routes if r.path=='/api/v1/wealth/portfolio/snapshot' and 'GET' in r.methods]
    assert len(paths)==1
    assert not any(getattr(r,'path',None)=='/api/v1/wealth/portfolio/snapshot' for r in wealth.router.routes)
    assert app.openapi()['paths']['/api/v1/wealth/portfolio/snapshot']['get']['operationId'].startswith('snapshot_')
    assert isinstance(app.state.wealth_portfolio_source,JointPortfolio)
    assert set(app.state.wealth_action_handlers)=={'plan_portfolio','recommend_products','revise_portfolio'}

@pytest.mark.asyncio
@pytest.mark.parametrize('mode,status,data',[
    ('HUMAN','WAITING',{}),('AGENT','PAUSED',{}),
    ('AGENT','WAITING',{'stop_new_submissions':True}),
    ('AGENT','WAITING',{'session_closed':True}),
])
async def test_joint_write_control_uses_actual_related_task(store,http_store,mode,status,data):
    from backend.app.models.wealth import WealthTaskORM
    from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
    _,bank,_=http_store
    provider=JointPortfolio(Path(__file__).resolve().parents[2],bank_factory=lambda:bank)
    async with store() as db:
        db.add(WealthTaskORM(task_id='joint-control',user_id='U1',session_id='joint-control-session',
            account_id='A1',context_id='legacy-v1',version=1,status=status,control_mode=mode,data=data))
        await db.commit()
        facts,_=provider.bind(db,'U1',bank);access=JointAccess(facts)
        with pytest.raises(CandidateError,match='TASK_WRITE_BLOCKED'):
            await access.resolve('U1','legacy-v1',write=True)
        assert (await access.resolve('U1','legacy-v1',write=False))['can_read'] is True

@pytest.mark.asyncio
async def test_final_prepare_c_share_uses_its_actual_r4_not_a_share_r2(store,http_store,monkeypatch):
    client,bank,source,_=await setup(store,http_store,monkeypatch)
    async with store() as db:
        row=await submit_assessment(db,'U1',AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,
            profile_version=1,adult=True,full_capacity=True,answers={k:1 for k,_,_ in QUESTIONS}))
        await confirm_assessment(db,'U1',row['assessment_id'])
        a=await db.get(WealthProductVersionORM,'MF002:1');c=await db.get(WealthProductVersionORM,'MF004:1')
        a.rules={**a.rules,'share_class':'A','fund_family_id':'isolated-share-family'}
        c.rules={**c.rules,'share_class':'C','fund_family_id':'isolated-share-family'}
        assert a.risk_level==2 and c.risk_level==4
        await db.commit()
    plan=await source.record(bank,[('MF004','growth','0.000001')])
    source.plan['selected_positions'][0]['share_class']='C';source.plan['selected_positions'][0]['family_id']='isolated-share-family'
    response=await client.post('/api/v1/wealth/portfolio/plans/'+plan['portfolio_id']+'/prepare',json={'expected_version':plan['version'],'snapshot_id':plan['snapshot_id']})
    assert response.status_code==409 and response.json()['detail']['code']=='SUITABILITY_MISMATCH',response.text
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert (await db.get(AccountORM,'A1')).balance_fen==2_000_000
