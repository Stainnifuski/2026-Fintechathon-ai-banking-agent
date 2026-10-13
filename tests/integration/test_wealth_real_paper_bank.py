"""Actual saved public dataset through bank mapping/ledger, in two isolated DBs.

Bank authorization helper is an isolated bank-layer fixture; browser MFA is
recorded separately. Public product facts/NAV are never substituted by fixtures.
"""
import json
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,assess,authorize
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.wealth_research import import_dataset,initialize,select_context
from backend.app.schemas.wealth import InvestmentCommand,AssessmentSubmission
from backend.app.integrations.wealth_authorization import submit_assessment,confirm_assessment,WealthPolicyError
from backend.app.domain.wealth_risk import QUESTIONS,QUESTIONNAIRE_VERSION
from backend.app.models import AccountORM,TransactionORM
from backend.app.models.wealth import WealthOrderORM,WealthCashHoldORM,WealthNavORM,WealthLotORM
from backend.app.services.bank.transfer_service import BankError

ROOT=Path(__file__).resolve().parents[2]
BASE=datetime(2026,10,13,6,tzinfo=timezone.utc)

async def initialized(db):
    payload=json.loads((ROOT/'config/wealth/paper_products.json').read_text(encoding='utf8'))
    result=await import_dataset(db,payload)
    assert result['ok'],result
    ctx=await initialize(db,'U1','normal',dataset_key=result['dataset_key'])
    await select_context(db,'U1',ctx['context_id'],1)
    return payload,ctx

def cmd(code,account,key,direction='purchase',units='0.0000'):
    return InvestmentCommand(operation_id=key,direction=direction,product_id=code,product_version=1,
        disclosure_version=1,account_id=account,amount_fen=100000 if direction=='purchase' else 0,units=units)

@pytest.mark.asyncio
async def test_seven_saved_real_products_settle_and_redeem_without_future_nav(store,monkeypatch):
    current=[BASE]
    async def clock(db,account_id=None):return current[0]
    monkeypatch.setattr(bank,'business_now',clock)
    async with store() as db:
        payload,ctx=await initialized(db);await assess(db)
        codes=payload['joint_samples'];assert len(codes)==len(set(codes))==7
        commands=[cmd(code,ctx['account_id'],'paper-'+code) for code in codes]
        for command in commands:
            preview=await bank.precheck(db,'U1',command)
            assert preview['rules']['purchase_fee_rate']=='0'
            assert preview['disclosure']['underlying_source_kind']=='real'
            assert preview['estimated_nav_date']<preview['pricing_date']
        auth=[await authorize(db,c) for c in commands]
        accepted=await bank.accept_many(db,'U1',list(zip(commands,auth)))
        assert all(o['status']=='ACCEPTED' for o in accepted)
        account=await db.get(AccountORM,ctx['account_id']);assert account.balance_fen==2000000
        assert await db.scalar(select(func.sum(WealthCashHoldORM.amount_fen)).where(WealthCashHoldORM.status=='HELD'))==700000
        # Waiting for T+1 is not reported as a settled holding.
        waiting=await bank.settle(db,commands[0].operation_id);assert waiting['status']=='PENDING_PRICING'
        current[0]=BASE+timedelta(days=1)
        for c in commands:
            result=await bank.settle(db,c.operation_id);assert result['status']=='SETTLED',result
            assert result['details']['source_nav_date']<result['details']['simulated_pricing_date']
            assert result['details']['actual']['fee_fen']==0
            assert (await bank.settle(db,c.operation_id))['status']=='SETTLED'
        assert await db.scalar(select(func.count()).select_from(WealthLotORM).where(WealthLotORM.user_id=='U1'))==7
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.type=='investment_purchase'))==7
        await db.refresh(account);assert account.balance_fen==1300000
        expected=next(p for p in payload['products'] if p['product_id']=='000015')['history']
        assert await db.scalar(select(func.count()).select_from(WealthNavORM).where(WealthNavORM.version_id=='000015:1'))==len(expected)
        assert (await db.scalar(select(func.max(WealthNavORM.nav_date)).where(WealthNavORM.version_id=='000015:1')))<BASE.date()
        # Ordinary redemption: shares consumed at T+1, cash only at T+2.
        redemption=cmd('000015',ctx['account_id'],'paper-redeem','redeem','10.0000')
        aid=await authorize(db,redemption);await bank.accept_many(db,'U1',[(redemption,aid)])
        current[0]=BASE+timedelta(days=2)
        pending=await bank.settle(db,redemption.operation_id);assert pending['status']=='PENDING_CASH'
        await db.refresh(account);assert account.balance_fen==1300000
        current[0]=BASE+timedelta(days=3)
        done=await bank.settle(db,redemption.operation_id);assert done['status']=='SETTLED'
        cash=done['details']['actual']['cash_fen'];await db.refresh(account);assert account.balance_fen==1300000+cash
        await bank.settle(db,redemption.operation_id);await db.refresh(account);assert account.balance_fen==1300000+cash
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.type=='investment_redemption'))==1

@pytest.mark.asyncio
@pytest.mark.parametrize('amount',[0,1,100000])
async def test_final_r2_to_saved_real_r4_rejected_before_cash_hold_even_zero_tiny(store,monkeypatch,amount):
    async def clock(db,account_id=None):return BASE
    monkeypatch.setattr(bank,'business_now',clock)
    async with store() as db:
        _,ctx=await initialized(db)
        a=await submit_assessment(db,'U1',AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,
            profile_version=1,adult=True,full_capacity=True,answers={k:1 for k,_,_ in QUESTIONS}))
        await confirm_assessment(db,'U1',a['assessment_id']);assert a['capacity_level']==2
        c=cmd('020458',ctx['account_id'],'paper-risk-'+str(amount)).model_copy(update={'amount_fen':amount})
        with pytest.raises(WealthPolicyError,match='风险'):await bank.precheck(db,'U1',c)
        with pytest.raises(WealthPolicyError,match='风险'):await bank.accept_many(db,'U1',[(c,'untrusted-proof')])
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        assert (await db.get(AccountORM,ctx['account_id'])).balance_fen==2000000

@pytest.mark.asyncio
async def test_bank_public_higher_rating_or_class_conflict_not_downgraded(store,monkeypatch):
    async def clock(db,account_id=None):return BASE
    monkeypatch.setattr(bank,'business_now',clock)
    from backend.app.models.wealth import WealthProductVersionORM
    async with store() as db:
        _,ctx=await initialized(db);await assess(db)
        row=await db.get(WealthProductVersionORM,'000015:1')
        policy={**row.rules['execution_policy'],'public_ratings':[{'risk_level':4,'reference_sha256':'d'*64}]}
        row.rules={**row.rules,'execution_policy':policy};await db.commit()
        with pytest.raises(BankError,match='PUBLIC_HIGHER_RISK_REVIEW_REQUIRED'):
            await bank.precheck(db,'U1',cmd('000015',ctx['account_id'],'higher-risk'))
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0

@pytest.mark.asyncio
async def test_saved_real_paper_cancel_releases_hold_without_refund(store,monkeypatch):
    async def clock(db,account_id=None):return BASE
    monkeypatch.setattr(bank,'business_now',clock)
    async with store() as db:
        _,ctx=await initialized(db);await assess(db)
        command=cmd('000015',ctx['account_id'],'paper-cancel-exact')
        auth=await authorize(db,command)
        await bank.accept_many(db,'U1',[(command,auth)])
        assert (await bank.cancellation_eligibility(db,'U1',command.operation_id))['eligibility']=='ALLOWED'
        assert (await bank.cancel(db,'U1',command.operation_id))['status']=='CANCELLED'
        assert (await bank.cancel(db,'U1',command.operation_id))['status']=='CANCELLED'
        assert (await db.get(AccountORM,ctx['account_id'])).balance_fen==2000000
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM).where(WealthCashHoldORM.status=='HELD'))==0
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.type.like('investment%')))==0

