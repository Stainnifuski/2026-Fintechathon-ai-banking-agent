"""Explicit disposable admin fixtures. Seeded confirmations are synthetic evidence."""
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from sqlalchemy import select
from ...domain.wealth_scenarios import SCENARIOS
from ...domain.wealth_synthetic import fingerprint
from ...models import UserORM
from ...models.wealth import WealthOrderORM,WealthAssessmentORM
from ...models.wealth_research import WealthContextORM
from ...schemas.wealth import AssessmentSubmission,InvestmentCommand,PersonalConfirmation
from ...domain.wealth_risk import QUESTIONS,QUESTIONNAIRE_VERSION
from ...integrations import wealth_authorization as policy
from . import wealth_research as research,wealth_service as bank


async def initialize_all(db) -> list[dict]:
    results=[]
    for i,(scenario,fixture) in enumerate(SCENARIOS.items(),1):
        uid=f'WSC{i:02}'
        user=await db.get(UserORM,uid)
        if user is None:
            db.add(UserORM(user_id=uid,name='虚构场景'+fixture['label'],phone=f'1399000{i:04}',id_card='fictional-scenario',status='active')); await db.flush()
        result=await research.initialize(db,uid,scenario,fixture.get('balance_fen',2000000))
        context=await db.get(WealthContextORM,result['context_id'])
        if context.data.get('fixture_ready'): results.append({**result,'ready':True}); continue
        answers={key:1 if fixture.get('capacity')==2 else 3 for key,_,_ in QUESTIONS}
        assessment=await policy.submit_assessment(db,uid,AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,profile_version=1,adult=True,full_capacity=True,answers=answers))
        await policy.confirm_assessment(db,uid,assessment['assessment_id'])
        selection=await research.context_list(db,uid)
        await research.select_context(db,uid,context.context_id,selection['version'])
        async def order(product_id,direction='purchase',amount=100000,units='0.0000'):
            op='WFIX'+fingerprint([uid,product_id,direction])[:32]
            existing=await db.get(WealthOrderORM,op)
            if existing: return bank.order_view(existing)
            cmd=InvestmentCommand(operation_id=op,product_id=product_id,product_version=1,disclosure_version=1,account_id=context.account_id,direction=direction,amount_fen=amount if direction=='purchase' else 0,units=units)
            await bank.precheck(db,uid,cmd)
            auth=await policy.new_preview_authorization(db,uid,cmd); aid=auth.authorization_id; digest=auth.param_digest; await db.commit()
            await policy.record_confirmation(db,uid,aid,PersonalConfirmation(digest=digest,disclosure_version=1,accepted=True))
            await bank.demo_verify_authorizations(db,uid,[aid])
            return (await bank.accept_many(db,uid,[(cmd,aid)]))[0]
        if fixture.get('accepted') or fixture.get('seed_holding'):
            if fixture.get('locked'): context.data={**context.data,'business_at':'2026-09-30T14:00:00+08:00'}; await db.commit()
            amount=900000 if fixture.get('second_share') else 100000
            buy=await order(fixture['target'],amount=amount)
            if fixture.get('second_share'): second=await order('S008',amount=amount)
            if fixture.get('seed_holding'):
                context.data={**context.data,'business_at':'2026-10-02T19:00:00+08:00'}; await db.commit()
                await bank.settle(db,buy['operation_id'])
                if fixture.get('second_share'): await bank.settle(db,second['operation_id'])
            if fixture.get('seed_redemption'):
                facts=await bank.assets(db,uid)
                total=sum((Decimal(l['redeemable_units']) for l in facts['lots'] if l['product_id']==fixture['target']),Decimal(0))
                sell=await order(fixture['target'],direction='redeem',units=format(total,'.4f'))
                if scenario=='cash_delayed':
                    context.data={**context.data,'business_at':'2026-10-06T19:00:00+08:00'}; await db.commit()
                    await bank.settle(db,sell['operation_id'])
            context.data={**context.data,'fixture_operation_id':buy['operation_id']}; await db.commit()
        if fixture.get('expired_assessment'):
            row=await db.get(WealthAssessmentORM,assessment['assessment_id']); row.expires_at=datetime.now(timezone.utc)-timedelta(days=1)
        context.data={**context.data,'fixture_ready':True,'seeded_identity_confirmation_is_synthetic':True}
        from .audit_service import record_bank_event
        record_bank_event(db,event_key=context.context_id+':fixture-ready',subject_user_id=uid,action='investment_scenario_fixture',result='success',facts={'context_id':context.context_id,'scenario':scenario,'synthetic_confirmation':True},decision_chain=['explicit_admin_fixture','not_human_confirmation','bank_rules_used_for_orders'])
        await db.commit(); results.append({**result,'user_id':uid,'phone':user.phone if user else f'1399000{i:04}','scenario':scenario,'ready':True})
    return results
