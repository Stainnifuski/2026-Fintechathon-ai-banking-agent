"""Frozen source timestamp must not become the live eligibility-read timestamp."""
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.services.orchestration.wealth_portfolio_model import adapt_candidate_group

def test_money_card_keeps_unknown_real_nav_label_and_rule_source_date():
    root=Path(__file__).resolve().parents[2]
    p=next(x for x in json.loads((root/'config/wealth/paper_products.json').read_bytes())['products'] if x['category']=='cash')
    product=SimpleNamespace(product_id=p['product_id'],name=p['name'],product_version=p['product_version'],disclosure_version=p['disclosure_version'],share_class=p['share_class'],source_kind='real',execution_policy=p['rules']['execution_policy'])
    family={'family_id':'exact-code:'+p['product_id'],'default_share':{'product_id':p['product_id']},'bucket':'cash_management','substantive_category':'money_market',
        'shares':[{'product_id':p['product_id'],'product_level':1,'purchase_eligible':True,'checked_at':'2026-10-14T02:00:00+00:00','reason_codes':[],'evidence':{'bank_fact':{'minimum_fen':10000}}}]}
    raw={'status':'ok','source_kind':'simulated','fixture_only':False,'items':[family],'pipeline':{'reason_codes':[],'exclusions':{},'risk_explanation':None}}
    card=adapt_candidate_group(raw,SimpleNamespace(products={p['product_id']:product}),CandidateFilter(mode='general')).items[0]
    assert card.share_classes[0].data_as_of==datetime.fromisoformat(p['rules']['execution_policy']['quote']['available_at'])
    assert '真实NAV未知' in card.share_classes[0].data_source
    assert '货币／项目R1' in card.reason and '收益或质量排名' in card.reason
