"""100 distinct rule/data evaluation cases; real-model results are reported separately."""
import json
from pathlib import Path
from copy import deepcopy
from datetime import date, timedelta
import pytest
from backend.app.domain.wealth_analytics import metrics,correlation,concentration
from backend.app.domain.wealth import purchase,redeem
from backend.app.domain.wealth_allocation import allocate,candidates
from backend.app.services.ai.wealth_dialogue import offline,resolve
from backend.app.services.ai.wealth_model import Intent
from backend.app.services.bank.wealth_provider import normalize_history,parse_import
from backend.app.services.bank.wealth_research import validate
from backend.app.services.bank.wealth_knowledge import documents,tokens
from backend.app.domain.wealth_synthetic import generate
from backend.app.schemas.wealth import InvestmentCommand,WealthResearchQuery

CASES=json.loads((Path(__file__).parents[1]/'fixtures/wealth_v12_eval.json').read_text(encoding='utf-8'))


def history(prices,dividends=None):
    return [{'date':(date(2026,9,1)+timedelta(days=i)).isoformat(), 'nav':str(p),'dividend_per_unit':str((dividends or [0]*len(prices))[i])} for i,p in enumerate(prices)]


def sample_products():
    rules={'holding_days':0,'confirm_business_days':1,'cash_business_days':1,'calendar_version':'synthetic-weekdays-v1','purchase_fee_rate':'0','purchase_paused':False,'min_amount_fen':10000,'max_amount_fen':200000000}
    return [dict(product_id=pid,name=name,product_risk_level=risk,status='on_sale',rules=dict(rules),nav='1.00000000',nav_date='2026-09-30',category=category,fund_family_id=family,version=1,disclosure={'version':1},dataset_key='synthetic-v12:20260930-factor-v1') for pid,name,risk,category,family in [('C','现金A',1,'cash','FC'),('B','债券A',2,'bond','FB'),('M','混合A',3,'mixed','FM'),('E','权益A',5,'equity','FE')]]


@pytest.fixture(scope='module')
def corpus(): return documents(generate()['products'])


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_rule_evaluation(case,corpus):
    spec=case['expected']; kind=case['category']; op=spec['op']
    if kind=='knowledge':
        doc=next(r for r in corpus if r['document_id']==spec['document_id'])
        assert all(term in doc['content'] for term in spec['terms'])
        assert len(tokens(case['input']) & tokens(doc['content']+' '+doc['title']))>=1
        assert doc['content_hash'] and doc['version'] and doc['license']
        assert doc['document_id'] in case['allowed_citations']
    elif kind=='analytics':
        if op=='metrics':
            result=metrics(history(spec['prices'],spec.get('dividends')),as_of=spec.get('as_of','2026-09-30'),period='since',dividends_verified=spec.get('verified',False))
            for k,v in spec['result'].items(): assert result[k]==v
        elif op=='purchase':
            result=purchase(spec['amount'],spec['nav'],spec['rate']); assert result.fee_fen==spec['fee_fen']; assert result.units==spec['units']
        elif op=='redeem':
            result=redeem(spec['units'],spec['nav'],spec['rate'],lot_units=spec['lot_units'],lot_cost_fen=spec['cost'])
            for k,v in spec['result'].items(): assert getattr(result,k)==v
        elif op=='correlation': assert correlation(history(spec['a']),history(spec['b']))[spec['field']]==spec['value']
        elif op=='concentration': assert concentration(spec['holdings'])['overconcentrated_families']==spec['families']
    elif kind=='recommendation':
        rows=sample_products()
        for change in spec.get('changes',[]):
            p=next(p for p in rows if p['product_id']==change['id']); p.update(change.get('fields',{})); p['rules'].update(change.get('rules',{}))
        args={'budget_fen':1000000,'reserve_fen':200000,'capacity':3,'horizon_days':365,'excluded':[],**spec.get('args',{})}
        if op=='error':
            with pytest.raises(ValueError): allocate(rows,**args)
        else:
            result=allocate(rows,**args)
            if 'products' in spec: assert [i['product_id'] for i in result['items']]==spec['products']
            assert result['investment_fen']+result['retained_cash_fen']==args['budget_fen']
            assert result['retained_cash_fen']>=args['reserve_fen'] and len(result['items'])<=5
            assert len({i['fund_family_id'] for i in result['items']})==len(result['items'])
            for key,value in spec.get('result',{}).items(): assert result[key]==value
    elif kind=='dialogue':
        if op=='intent':
            result=offline(case['input']).model_dump()
            for key,value in spec['result'].items(): assert result[key]==value
        elif op=='resolve': assert resolve(case['input'],spec['products'],spec['candidates'])==spec['product_id']
        elif op=='ambiguity':
            with pytest.raises(ValueError): resolve(case['input'],spec['products'],spec['candidates'])
    elif kind=='safety':
        # Typed boundaries reject unknown tools, credential fields and malformed financial values.
        if op=='intent_schema':
            with pytest.raises(ValueError): Intent.model_validate(spec['payload'])
        elif op=='command_schema':
            with pytest.raises(ValueError): InvestmentCommand.model_validate(spec['payload'])
        elif op=='research_schema':
            with pytest.raises(ValueError): WealthResearchQuery.model_validate(spec['payload'])
        elif op=='history':
            with pytest.raises(ValueError): normalize_history(spec['records'],'2026-09-30T19:00:00+08:00')
        elif op=='import':
            with pytest.raises(ValueError): parse_import(spec['raw'].encode(),spec['format'])
        elif op=='dataset': assert validate(spec['payload'])
        elif op=='resolve':
            with pytest.raises(ValueError): resolve(case['input'],spec['products'],spec['candidates'])
    else: raise AssertionError('Unknown evaluation category')
