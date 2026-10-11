"""Unified wealth MFA and preserved historical evidence, isolated SQLite/PostgreSQL."""
import re
from datetime import datetime,timezone
import pytest
from sqlalchemy import select,text
from tests.integration.test_wealth_bank import store,http_store,assess,authorize,command
from tests.integration.wealth_mfa_helpers import sms_verify
from backend.app.models.wealth import WealthAuthorizationORM,WealthVerificationORM,WealthOrderORM
from backend.app.models import UserORM,AuditLogORM
from backend.app.security import sms
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.wealth_migration import upgrade_verification_methods
from backend.app.integrations.wealth_authorization import WealthPolicyError

@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setenv('MFA_SMS_ENABLED','true');monkeypatch.setenv('SMS_DEV_INBOX_ENABLED','true')
    mock=sms.MockSmsProvider();sms.set_sms_provider(mock)
    yield mock
    sms.set_sms_provider(None)

async def prepare(client,store):
    async with store() as db:await assess(db)
    plan=(await client.post('/api/v1/wealth/plans',json={'account_id':'A1','budget_fen':500000,
        'reserve_fen':0,'horizon_days':365,'version':1})).json()
    base='/api/v1/wealth/plans/'+plan['plan_id'];version=plan['version']
    preview=(await client.post(base+'/preview',json={'version':version})).json()
    confirm={'version':version,'items':[{'authorization_id':v['authorization_id'],'digest':v['digest'],
        'disclosure_version':v['command']['disclosure_version'],'accepted':True} for v in preview['items']]}
    return plan,base,preview,confirm

@pytest.mark.asyncio
async def test_group_sms_confirmation_complete_set_replay_and_execution(store,http_store):
    client,local,app=http_store
    plan,base,view,body=await prepare(client,store)
    assert len(view['items'])>1
    assert (await client.post(base+'/verify/demo',json={})).status_code==404
    before=await sms_verify(client,base,version=1,reference=view['verification_reference_id'])
    assert before.status_code==409
    assert (await client.post(base+'/confirm',json={**body,'items':body['items'][:1]})).status_code==409
    assert (await client.post(base+'/confirm',json=body)).status_code==200
    # Reuse still unconsumed challenge produced before confirmation (no proof accepted before consent).
    sent=(await client.get('/api/v1/auth/sms/dev-inbox')).json()
    proof=re.search(r'验证码 (\d{6})',sent[0]['content']).group(1)
    from backend.app.models import SmsChallengeORM
    async with store() as db:
        challenge=await db.scalar(select(SmsChallengeORM).where(SmsChallengeORM.reference_id==view['verification_reference_id']))
        cid=challenge.challenge_id
    wrong=await client.post(base+'/verify',json={'version':1,'method':'sms','challenge_id':cid,
        'proof':'000000' if proof!='000000' else '111111'})
    assert wrong.status_code==401
    # Internal endpoint still enforces exact set, even a trusted caller cannot omit a product.
    from backend.app.integrations.bank_api import BankApiError
    with pytest.raises(BankApiError) as error:
        await local.call('POST','/verify/batch',{'subject_user_id':'U1','plan_id':plan['plan_id'],
            'version':1,'method':'sms','proof':proof,'challenge_id':cid,'authorization_ids':[view['items'][0]['authorization_id']]})
    assert error.value.code=='STALE_PLAN'
    response=await client.post(base+'/verify',json={'version':1,'method':'sms','challenge_id':cid,'proof':proof})
    assert response.status_code==200,response.text
    for _ in range(2):
        result=await client.post(base+'/execute',json={'version':1});assert result.status_code==200,result.text
    async with store() as db:
        evidence=list(await db.scalars(select(WealthVerificationORM)))
        assert len(evidence)==len(view['items'])
        assert all(e.method=='SMS' and e.simulated and not e.replacement_required for e in evidence)
        assert len(list(await db.scalars(select(WealthOrderORM))))==len(view['items'])
        for audit in await db.scalars(select(AuditLogORM)):
            assert proof not in str(audit.params) and 'proof' not in audit.params

@pytest.mark.asyncio
async def test_old_group_challenge_cannot_authorize_changed_version_and_wrong_owner(store,http_store):
    client,_,app=http_store
    plan,base,view,body=await prepare(client,store)
    sent=await client.post('/api/v1/auth/sms/send',json={'purpose':'wealth','reference_id':view['verification_reference_id']})
    proof=re.search(r'验证码 (\d{6})',(await client.get('/api/v1/auth/sms/dev-inbox')).json()[0]['content']).group(1)
    changed=await client.put(base,json={**plan['request'],'version':1,'budget_fen':600000});assert changed.status_code==200,changed.text
    latest=(await client.post(base+'/preview',json={'version':2})).json()
    assert latest['verification_reference_id']!=view['verification_reference_id']
    body={'version':2,'items':[{'authorization_id':v['authorization_id'],'digest':v['digest'],
        'disclosure_version':1,'accepted':True} for v in latest['items']]}
    assert (await client.post(base+'/confirm',json=body)).status_code==200
    request={'version':2,'method':'sms','challenge_id':sent.json()['challenge_id'],'proof':proof}
    denied=await client.post(base+'/verify',json=request)
    assert denied.status_code==409 and denied.json()['detail']['code']=='SMS_CHALLENGE_MISMATCH'
    from backend.app.middleware.auth import get_current_user
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
    assert (await client.post(base+'/verify',json=request)).status_code==404
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U1'}

@pytest.mark.asyncio
async def test_group_totp_once_for_multiple_products(store,http_store):
    import pyotp
    client,_,_=http_store
    _,base,view,body=await prepare(client,store)
    secret=pyotp.random_base32()
    async with store() as db:
        user=await db.get(UserORM,'U1');user.totp_secret=secret;user.totp_enabled=True;await db.commit()
    assert (await client.post(base+'/confirm',json=body)).status_code==200
    response=await client.post(base+'/verify',json={'version':1,'method':'totp','proof':pyotp.TOTP(secret).now()})
    assert response.status_code==200,response.text
    async with store() as db:
        evidence=list(await db.scalars(select(WealthVerificationORM)))
        assert len(evidence)==len(view['items']) and all(e.method=='TOTP' and not e.simulated for e in evidence)
    assert (await client.post(base+'/execute',json={'version':1})).status_code==200

@pytest.mark.asyncio
async def test_historical_click_denied_but_submitted_order_preserved(store):
    async with store() as db:
        await assess(db);cmd=command('already-accepted');aid=await authorize(db,cmd)
        await bank.accept_many(db,'U1',[(cmd,aid)])
        evidence=await db.get(WealthVerificationORM,aid);evidence.method='SIMULATED_CLICK';evidence.simulated=True;await db.commit()
        assert (await bank.accept_many(db,'U1',[(cmd,aid)]))[0]['operation_id']==cmd.operation_id
        fresh=command('old-click-unsubmitted');fid=await authorize(db,fresh)
        evidence=await db.get(WealthVerificationORM,fid);evidence.method='SIMULATED_CLICK';evidence.simulated=True;await db.commit()
        with pytest.raises(WealthPolicyError) as denied:await bank.accept_many(db,'U1',[(fresh,fid)])
        assert denied.value.code=='SIMULATED_VERIFICATION_DENIED'

@pytest.mark.asyncio
async def test_legacy_method_constraint_upgrade_preserves_all_evidence(store):
    async with store() as db:
        await assess(db);aid=await authorize(db,command('migration-evidence'))
        db.expunge_all()
        before=list((await db.execute(text('SELECT * FROM wealth_verifications'))).all())
        await db.execute(text('DROP TABLE wealth_verifications'))
        await db.execute(text("""CREATE TABLE wealth_verifications (
            authorization_id VARCHAR(64) PRIMARY KEY REFERENCES wealth_authorizations(authorization_id),
            method VARCHAR(32) NOT NULL CHECK(method IN ('TOTP','SIMULATED_CLICK')),
            provider_version VARCHAR(32) NOT NULL, simulated BOOLEAN NOT NULL,
            replacement_required BOOLEAN NOT NULL, verified_at TIMESTAMP WITH TIME ZONE NOT NULL)"""))
        for r in before:
            await db.execute(text('INSERT INTO wealth_verifications VALUES (:a,:m,:p,:s,:r,:t)'),dict(zip(['a','m','p','s','r','t'],r)))
        await upgrade_verification_methods(db);await upgrade_verification_methods(db)
        assert list((await db.execute(text('SELECT * FROM wealth_verifications'))).all())==before
        await db.execute(text("UPDATE wealth_verifications SET method='SMS'"));await db.commit()
