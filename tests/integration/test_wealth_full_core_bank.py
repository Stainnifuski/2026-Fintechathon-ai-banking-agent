"""Affected bank mapping boundary only; genuine saved products, two isolated DBs."""
import json
from pathlib import Path
from datetime import datetime,timezone
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store
from backend.app.services.bank.wealth_research import import_dataset,initialize,select_context
from backend.app.services.bank import wealth_service as bank
from backend.app.schemas.wealth import AssessmentSubmission,InvestmentCommand
from backend.app.domain.wealth_risk import QUESTIONS,QUESTIONNAIRE_VERSION
from backend.app.integrations.wealth_authorization import submit_assessment,confirm_assessment,WealthPolicyError
from backend.app.models.wealth import WealthOrderORM,WealthCashHoldORM,WealthProductVersionORM
from backend.app.services.bank.transfer_service import BankError
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.asyncio
async def test_full_core_samples_versions_and_final_risk_boundary(store,monkeypatch):
    payload=json.loads((ROOT/'config/wealth/paper_products.json').read_bytes())
    legacy=json.loads((ROOT/'config/wealth/paper_products_legacy_v1.json').read_bytes())
    samples=[]
    for category in ('cash','bond','mixed','equity'):
        group=sorted([p for p in payload['products'] if p['category']==category],key=lambda p:p['product_id'])
        samples.extend([group[0],group[len(group)//2],group[-1]])
    legacy_one=legacy['products'][0]
    overlap=next(p for p in payload['products'] if p['product_id']==legacy_one['product_id'])
    chosen={p['product_id']:p for p in samples+[overlap]}
    async def clock(db,account_id=None):return datetime(2026,10,13,6,tzinfo=timezone.utc)
    monkeypatch.setattr(bank,'business_now',clock)
    async with store() as db:
        first=await import_dataset(db,{**legacy,'products':[legacy_one]});assert first['ok'],first
        before=(await db.get(WealthProductVersionORM,legacy_one['product_id']+':1')).rules
        imported=await import_dataset(db,{**payload,'products':list(chosen.values())});assert imported['ok'],imported
        assert (await db.get(WealthProductVersionORM,legacy_one['product_id']+':1')).rules==before
        assert overlap['product_version']>legacy_one['product_version']
        ctx=await initialize(db,'U1','normal',dataset_key=imported['dataset_key']);await select_context(db,'U1',ctx['context_id'],1)
        from backend.app.domain.wealth_paper_policy import policy_sha
        from backend.app.integrations.wealth_portfolio_bank_facts import native_product_evidence
        compact=await bank.products(db,'U1',facts_only=True,policy_references=True)
        assert {p['product_id'] for p in compact}==set(chosen)
        subset=await bank.products(db,'U1',facts_only=True,product_ids=[overlap['product_id'],'missing-code'])
        assert [p['product_id'] for p in subset]==[overlap['product_id']]
        assert await bank.products(db,'U1',facts_only=True,product_ids=[])==[]
        for p in compact:
            assert p['execution_policy_sha256']==policy_sha(chosen[p['product_id']]['rules']['execution_policy'])
            assert 'execution_policy' not in p['rules'] and native_product_evidence(p)['rules_complete']
        missing=await db.get(WealthProductVersionORM,overlap['product_id']+':'+str(overlap['product_version']))
        original_disclosure=missing.disclosure
        missing.disclosure={k:v for k,v in original_disclosure.items() if k!='seller'};await db.flush()
        broken=next(p for p in await bank.products(db,'U1',facts_only=True) if p['product_id']==overlap['product_id'])
        assert not native_product_evidence(broken)['rules_complete']
        scoped_broken=await bank.products(db,'U1',facts_only=True,product_ids=[overlap['product_id']])
        assert not native_product_evidence(scoped_broken[0])['rules_complete']
        missing.disclosure=original_disclosure;await db.flush()
        a=await submit_assessment(db,'U1',AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,profile_version=1,adult=True,full_capacity=True,answers={k:1 for k,_,_ in QUESTIONS}))
        await confirm_assessment(db,'U1',a['assessment_id']);assert a['capacity_level']==2
        equity=next(p for p in samples if p['category']=='equity')
        for amount in (0,1,100000):
            cmd=InvestmentCommand(operation_id='core-risk-'+str(amount),direction='purchase',product_id=equity['product_id'],product_version=equity['product_version'],disclosure_version=equity['disclosure_version'],account_id=ctx['account_id'],amount_fen=100000).model_copy(update={'amount_fen':amount})
            with pytest.raises(WealthPolicyError):await bank.precheck(db,'U1',cmd)
            with pytest.raises(WealthPolicyError):await bank.accept_many(db,'U1',[(cmd,'not-authorized')])
        bond=next(p for p in samples if p['category']=='bond')
        cmd=InvestmentCommand(operation_id='core-positive',direction='purchase',product_id=bond['product_id'],product_version=bond['product_version'],disclosure_version=bond['disclosure_version'],account_id=ctx['account_id'],amount_fen=100000)
        assert (await bank.precheck(db,'U1',cmd))['rules']['purchase_fee_rate']=='0'
        row=await db.get(WealthProductVersionORM,bond['product_id']+':'+str(bond['product_version']))
        row.rules={**row.rules,'purchase_paused':True};await db.commit()
        with pytest.raises(BankError):await bank.precheck(db,'U1',cmd)
        row.rules={**row.rules,'purchase_paused':False,'execution_policy':{**row.rules['execution_policy'],'classification_conflict':True}};await db.commit()
        with pytest.raises(BankError):await bank.precheck(db,'U1',cmd)
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        money=next(p for p in samples if p['category']=='cash')
        assert money['real_unit_nav'] is None and money['price_source']=='project_simulated_fixed_one'
        assert money['rules']['execution_policy']['quote']['value']=='1' and money['risk_level']==1
        assert money['rules']['quick_redemption'] is False
        money_cmd=InvestmentCommand(operation_id='core-money',direction='purchase',product_id=money['product_id'],product_version=money['product_version'],disclosure_version=money['disclosure_version'],account_id=ctx['account_id'],amount_fen=100000)
        preview=await bank.precheck(db,'U1',money_cmd)
        assert preview['disclosure']['real_unit_nav'] is None
        assert preview['rules']['cash_business_days']==1
        from tests.integration.test_wealth_bank import authorize
        from datetime import timedelta
        auth=await authorize(db,money_cmd)
        await bank.accept_many(db,'U1',[(money_cmd,auth)])
        async def next_day(db,account_id=None):return datetime(2026,10,14,6,tzinfo=timezone.utc)
        monkeypatch.setattr(bank,'business_now',next_day)
        done=await bank.settle(db,money_cmd.operation_id)
        assert done['status']=='SETTLED' and done['details']['source_nav_date'] is None
        assert done['details']['price_source']=='project_simulated_fixed_one'
