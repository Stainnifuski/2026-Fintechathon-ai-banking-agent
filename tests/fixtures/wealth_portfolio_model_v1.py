"""第04步独立模型fixture，沿用资格事实，不改F01—F24。"""
from copy import deepcopy
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,field,NOW,FixtureSource
from backend.app.domain.wealth_portfolio_candidates import ProductIndex
from backend.app.domain.wealth_portfolio_order import CodeCategorySelector
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateService,SnapshotIndexCache
from backend.app.services.orchestration.wealth_portfolio import FixtureSnapshotSource
from backend.app.services.orchestration.wealth_portfolio_model import PortfolioModelProvider
from scripts.generate_wealth_portfolio_fixtures import bundle
from backend.app.schemas.wealth_portfolio import Fees


def model_fixture(level=4,holding=False):
    data,facts=fixture_pool(9,level,min(level,2))
    # The model consumes formal typed fee facts, including when holdings exist.
    # The qualification-only fixture intentionally omits them; leaving them out
    # here hid a production-only DTO/dict mismatch in the holdings path.
    for offer in facts.offers.values():
        offer.fee_rules=Fees(purchase_fee_rate='0',sales_service_fee_rate='0',redemption_fee_rules=[{'days_lt':999999,'rate':'0'}],missing_fields=[])
    texts=['本基金投资货币市场流动性工具。','本基金配置高等级短期债券。','本基金以债券票息收入为目标。','本基金重点投资于高股息股票。','本基金重点投资于科技产业股票。','本基金重点投资于基础设施股票。','本基金重点投资国债及政策性金融债。','本基金重点投资消费产业股票。','本基金配置短期现金工具。']
    buckets=['cash_management','stable','stable','growth','growth','growth','stable','growth','cash_management']
    for i,p in enumerate(data.products):
        family=i//2;p.bucket=buckets[family];p.substantive_category={'cash_management':'money_market','stable':'bond','growth':'equity'}[p.bucket]
        p.fields['strategy']=field(texts[family]);p.fields['issuer_raw']=field('虚构发行人'+str(family%3),'issuer')
    b=bundle();stamp=NOW.isoformat();b['summary']['as_of']=b['summary']['business_at']=stamp
    b['assessment'].update(capacity_level=level)
    b['assessment_provenance']={'source':facts.source_id,'version':facts.assessment.version,'read_at':stamp}
    b['data_versions']={'data_version':data.version,'daily_version':data.daily_version,'offers_version':facts.offers_version}
    if holding:
        b['accounts'][0].update(balance_fen=200000,held_fen=10000,available_fen=190000)
        b['summary'].update(cash_balance_fen=200000,held_fen=10000,available_cash_fen=190000,positions_market_value_fen=800000,total_assets_fen=1000000)
        b['lots']=[{'lot_id':'model-lot-stable','account_id':'fixture-account','product_id':'Q01A','product_version':1,'category':'bond','units':'3000.0000','redeemable_units':'3000.0000','market_value_fen':300000,'nav':'1','nav_date':'2026-10-09'},
                   {'lot_id':'model-lot-growth','account_id':'fixture-account','product_id':'Q03A','product_version':1,'category':'equity','units':'5000.0000','redeemable_units':'5000.0000','market_value_fen':500000,'nav':'1','nav_date':'2026-10-09'}]
    source=FixtureSource(facts);assets=FixtureSnapshotSource(b);selector=CodeCategorySelector()
    engine=CandidateService(source,SnapshotIndexCache(lambda v:ProductIndex(data)),selector,enable_fixture=True,clock=lambda:NOW)
    return PortfolioModelProvider(engine,assets),data,facts,b
