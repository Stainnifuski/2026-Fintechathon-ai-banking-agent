"""Approved policy checks, independent from issuer risk and share letters."""
from copy import deepcopy
from datetime import datetime,timezone
import pytest
from backend.app.domain.wealth_paper_policy import validate_policy,LEVELS,POLICY_VERSION

NOW=datetime(2026,10,13,tzinfo=timezone.utc)
def policy(category='bond'):
    return {'version':POLICY_VERSION,'product_id':'000015','category':category,'risk_level':LEVELS[category],
        'underlying_source_kind':'real','execution_source_kind':'simulated','classification_status':'verified',
        'classification_sha256':'a'*64,'material_sha256':'b'*64,'approved_at':'2026-10-13T00:00:00+00:00',
        'public_ratings':[],'quote':{'date':'2026-09-30','value':'1.208','available_at':'2026-10-07T00:00:00+00:00','reference_sha256':'c'*64}}

@pytest.mark.parametrize('category,level',[('bond',2),('mixed',3),('equity',4)])
def test_independent_project_grade_has_no_thirty_day_expiry(category,level):
    p=policy(category)
    assert validate_policy(p,'000015',category,level,NOW)==[]
    assert validate_policy(p,'000015',category,level,datetime(2027,1,1,tzinfo=timezone.utc))==[]

@pytest.mark.parametrize('change,reason',[
    ({'public_ratings':[{'risk_level':4,'reference_sha256':'d'*64}]},'PUBLIC_HIGHER_RISK_REVIEW_REQUIRED'),
    ({'classification_conflict':True},'PAPER_CLASSIFICATION_CONFLICT'),
    ({'category':'equity'},'PAPER_POLICY_IDENTITY_CONFLICT'),
    ({'product_id':'000016'},'PAPER_POLICY_IDENTITY_CONFLICT'),
    ({'expires_at':'2026-11-12'},'UNAPPROVED_POLICY_EXPIRY'),
    ({'approved_at':'2026-10-14T00:00:00+00:00'},'PAPER_POLICY_NOT_ACTIVE')])
def test_no_downgrade_identity_or_new_expiry(change,reason):
    p=policy();p.update(change)
    assert reason in validate_policy(p,'000015','bond',2,NOW)
