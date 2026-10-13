"""仅独立资格测试用的虚构事实；不默认注入应用，不改F01—F24。"""
from copy import deepcopy
from datetime import datetime,timezone
from backend.app.schemas.wealth_portfolio_data import (RawReference,Observation,FieldFact,EvidenceRecord,ProductFact,DataSnapshot)
from backend.app.schemas.wealth_portfolio_candidates import (FormalAssessment,BankPurchaseFact,CurrentFacts,ShareReference)
from backend.app.domain.wealth_portfolio_candidates import ProductIndex
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateService,SnapshotIndexCache

NOW=datetime(2026,10,9,6,0,tzinfo=timezone.utc)

def at(value):return datetime.fromisoformat(value.replace('Z','+00:00'))

def field(value,key='strategy'):
    ref=RawReference(path='fixture_only/qualification',sha256='a'*64,function='fixture_only',observed_at=at('2026-10-01T00:00:00Z'))
    return FieldFact(value=value,status='verified',observations=[Observation(value=value,raw_value=value,source='fixture_only',field=key,reference=ref,observed_at=ref.observed_at)])

def evidence(family,code_a,code_c,kind='family',share='A',risk=2):
    ref=RawReference(path='fixture_only/qualification',sha256='a'*64,function='fixture_only',observed_at=at('2026-10-01T00:00:00Z'))
    codes=[code_a,code_c] if kind=='family' else [code_a if share=='A' else code_c]
    classes={code_a:'A',code_c:'C'}
    return EvidenceRecord(evidence_id=family+':'+kind+':'+share,source_kind='fixture',kind=kind,source_role='fixture',url='https://fixture.invalid/document',
        codes=codes,share_classes={c:classes[c] for c in codes},channel='fixture-bank',published_on='2026-10-01',applicable_from='2026-10-01',applicable_to='2027-10-01',applicability_reason=None,
        observed_at=ref.observed_at,reference=ref,excerpt='明确同一基金份额关系 '+','.join(codes),family_id=family if kind=='family' else None,risk_level=risk if kind=='risk' else None)

def fixture_pool(count=8,user_level=2,product_level=2):
    products=[];offers={}
    for i in range(count):
        family=f'fixture-family-{i:02d}';a=f'Q{i:02d}A';c=f'Q{i:02d}C'
        for code,share in [(a,'A'),(c,'C')]:
            p=ProductFact(product_id=code,source_kind='fixture',share_class=share,product_version=1,disclosure_version=1,name='虚构红利名称',
                raw_category='bond',substantive_category='bond',classification_status='fixture_only',classification_basis='fixture_only',forms={},scope_status='verified',scope_reason=None,bucket='stable',
                fields={'issuer_raw':field('虚构基金公司甲','基金公司'),'strategy':field('本基金重点投资于高股息股票并配置债券票息资产。'),
                    'formal_risk':field(product_level,'risk_level'),'family_id':field(family,'family_id')},family_evidence=[evidence(family,a,c)])
            products.append(p)
            offers[code]=BankPurchaseFact(product_id=code,share_class=share,product_version=1,disclosure_version=1,rules_version='rules-1',source_id='fixture-source',source_kind='fixture',channel='fixture-bank',
                valid_from=at('2026-10-01T00:00:00Z'),valid_until=at('2027-10-01T00:00:00Z'),purchase_allowed=True,rules_complete=True,minimum_fen=100,maximum_fen=10000000,limit_kind='bounded',
                risk_level=product_level,risk_current_confirmed=True,risk_evidence_id='fixture-risk',holding_allowed=True,redemption_allowed=True)
    snapshot=DataSnapshot(adapter_version='fixture_only',version='fixture-data-1',fixed_version='fixture-fixed-1',daily_version='fixture-daily-1',evidence_version='fixture-proof-1',as_of=NOW,products=products,daily=[],input_references=[],coverage={})
    facts=CurrentFacts(user_id='fixture-user',source_id='fixture-source',mode='fixture',channel='fixture-bank',read_at=NOW,data_version=snapshot.version,daily_version=snapshot.daily_version,offers_version=1,
        assessment=FormalAssessment(user_id='fixture-user',assessment_id='fixture-assessment',version=1,source_id='fixture-source',source_kind='fixture',capacity_level=user_level,confirmed=True,purchase_allowed=True,
            expires_at=at('2027-10-01T00:00:00Z'),read_at=NOW),latest_assessment_id='fixture-assessment',latest_assessment_version=1,assessment_current_proven=True,offers=offers)
    return snapshot,facts

class FixtureSource:
    def __init__(self,facts):self.facts=facts;self.calls=0
    async def read_current(self,user_id,at):
        self.calls+=1;result=deepcopy(self.facts);result.read_at=at
        if result.assessment:result.assessment.read_at=at
        return result

class FixtureSelector:
    version='fixture-order-only'
    fixture_only=True
    def select(self,pool,excluded,limit):return sorted(f.family_id for f in pool if f.family_id not in excluded)[:limit]

def service(snapshot=None,facts=None,**kwargs):
    if snapshot is None:snapshot,facts=fixture_pool()
    source=FixtureSource(facts);cache=SnapshotIndexCache(lambda version:ProductIndex(snapshot))
    return CandidateService(source,cache,kwargs.pop('selector',FixtureSelector()),enable_fixture=kwargs.pop('enable_fixture',True),clock=lambda:NOW,**kwargs),source

def share(code,ratio='0.1'):
    return ShareReference(product_id=code,product_version=1,disclosure_version=1,rules_version='rules-1',share_class=code[-1],target_ratio_total=ratio)
