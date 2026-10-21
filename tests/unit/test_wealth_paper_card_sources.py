"""Frozen source timestamp must not become the live eligibility-read timestamp."""
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.services.orchestration.wealth_portfolio_model import adapt_candidate_group

def test_public_bond_card_keeps_rule_source_date_and_simulation_label():
    root=Path(__file__).resolve().parents[2]
    p=next(x for x in json.loads((root/'config/wealth/paper_products.json').read_bytes())['products'] if x['category']=='bond')
    product=SimpleNamespace(product_id=p['product_id'],name=p['name'],product_version=p['product_version'],disclosure_version=p['disclosure_version'],share_class=p['share_class'],source_kind='real',execution_policy=p['rules']['execution_policy'])
    family={'family_id':'exact-code:'+p['product_id'],'default_share':{'product_id':p['product_id']},'bucket':'stable','substantive_category':'bond',
        'shares':[{'product_id':p['product_id'],'product_level':2,'purchase_eligible':True,'checked_at':'2026-10-14T02:00:00+00:00','reason_codes':[],'evidence':{'bank_fact':{'minimum_fen':10000}}}]}
    raw={'status':'ok','source_kind':'simulated','fixture_only':False,'items':[family],'pipeline':{'reason_codes':[],'exclusions':{},'risk_explanation':None}}
    card=adapt_candidate_group(raw,SimpleNamespace(products={p['product_id']:product}),CandidateFilter(mode='general')).items[0]
    assert card.share_classes[0].data_as_of==datetime.fromisoformat(p['rules']['execution_policy']['quote']['available_at'])
    assert '真实公开资料' in card.share_classes[0].data_source and '模拟成交' in card.share_classes[0].data_source
    assert '债券／项目R2' in card.reason and '收益或质量排名' in card.reason
