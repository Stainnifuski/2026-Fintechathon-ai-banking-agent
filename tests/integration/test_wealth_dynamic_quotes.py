"""Isolated quote changes, order-date pricing and no ledger drift (SQLite/PG).

Synthetic observations below are explicitly test data, NOT real market evidence.
"""
import json
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from functools import lru_cache
from pathlib import Path
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.services.bank import wealth_service as bank,wealth_quotes as quotes
from backend.app.services.bank.wealth_research import import_dataset,initialize,select_context
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthLotORM,WealthNavORM,WealthOrderORM,WealthAuthorizationORM,WealthVerificationORM
from backend.app.models.wealth_research import WealthContextORM
from backend.app.schemas.wealth import InvestmentCommand,PersonalConfirmation
from backend.app.integrations.wealth_authorization import new_preview_authorization,record_confirmation
from backend.app.services.bank.transfer_service import BankError

@lru_cache(maxsize=1)
def material():
    payload=json.loads((Path(__file__).resolve().parents[2]/'config/wealth/paper_products.json').read_bytes())
    payload['products']=[next(p for p in payload['products'] if p['product_id']=='000003')]
    return payload

async def setup(db):
    data=deepcopy(material());result=await import_dataset(db,data);assert result['ok']
    ctx=await initialize(db,'U1','normal',dataset_key=result['dataset_key']);await select_context(db,'U1',ctx['context_id'],1)
    await assess(db)
    c=await db.get(WealthContextORM,ctx['context_id']);c.data={**c.data,'business_at':'2026-10-13T06:00:00+00:00'};await db.commit()
    p=data['products'][0]
    cmd=InvestmentCommand(operation_id='quote-isolated-purchase',direction='purchase',product_id=p['product_id'],
        product_version=p['product_version'],disclosure_version=p['disclosure_version'],account_id=ctx['account_id'],amount_fen=100000)
    return ctx,c,cmd

async def authorize(db,cmd):
    a=await new_preview_authorization(db,'U1',cmd);aid=a.authorization_id;digest=a.param_digest;await db.commit()
    await record_confirmation(db,'U1',aid,PersonalConfirmation(digest=digest,disclosure_version=cmd.disclosure_version,accepted=True))
    a=await db.get(WealthAuthorizationORM,aid);a.status='VALID';a.verified_at=datetime.now(timezone.utc)
    db.add(WealthVerificationORM(authorization_id=aid,method='TOTP',provider_version='isolated-test-only',simulated=False,replacement_required=False,verified_at=a.verified_at));await db.commit()
    return aid

def test_quote(points):
    return {'ok':True,'code':'000003','source':'ISOLATED_TEST_QUOTE_NOT_REAL','observed_at':'2026-10-15T00:00:00+00:00',
        'response_sha256':'f'*64,'points':[{'date':day,'nav':price,'available_at':'2026-10-15T00:00:00+00:00'} for day,price in points]}
test_quote.__test__=False

@pytest.mark.asyncio
async def test_order_nav_wait_and_new_valuation_preserve_cash_cost_shares_fills(store,http_store,monkeypatch):
    async with store() as db:
        ctx,c,cmd=await setup(db)
        aid=await authorize(db,cmd);await bank.accept_many(db,'U1',[(cmd,aid)]);await db.commit()
        async def only_latest(code):return test_quote([('2026-10-14','1.10000000')])
        monkeypatch.setattr(quotes,'observation',only_latest)
        c=await db.get(WealthContextORM,ctx['context_id']);c.data={**c.data,'business_at':'2026-10-15T02:00:00+00:00'};await db.commit()
        result=await bank.settle(db,cmd.operation_id);assert result['status']=='PENDING_PRICING';await db.commit()
        assert not list(await db.scalars(select(WealthLotORM)))
        async def published_order_day(code):return test_quote([('2026-10-13','1.00000000'),('2026-10-14','1.10000000')])
        monkeypatch.setattr(quotes,'observation',published_order_day)
        result=await bank.settle(db,cmd.operation_id);assert result['status']=='SETTLED';await db.commit()
        before=await bank.assets(db,'U1');lot=before['lots'][0];assert lot['nav_date']=='2026-10-14'
        from backend.app.services.orchestration.wealth_portfolio_joint import JointPortfolio
        from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate
        _,http_bank,_=http_store
        provider=JointPortfolio(Path(__file__).resolve().parents[2],bank_factory=lambda:http_bank)
        await provider.preload()
        _,service=provider.bind(db,'U1',http_bank)
        prior_plan=await service.create('U1',PortfolioCreate(context_id=ctx['context_id']))
        cash=before['cash'];units=lot['units'];cost=lot['cost_fen'];fill=deepcopy((await db.get(WealthOrderORM,cmd.operation_id)).details['actual'])
        async def changed(code):return test_quote([('2026-10-15','1.20000000')])
        monkeypatch.setattr(quotes,'observation',changed)
        await quotes.refresh(db,[lot['version_id']],datetime(2026,10,16,tzinfo=timezone.utc));await db.commit()
        after=await bank.assets(db,'U1');new=after['lots'][0]
        assert after['cash']==cash and (new['units'],new['cost_fen'])==(units,cost)
        assert new['market_value_fen']>lot['market_value_fen'] and new['unrealized_pnl_fen']>lot['unrealized_pnl_fen']
        assert after['asset_summary']['total_assets_fen']>before['asset_summary']['total_assets_fen']
        assert (await db.get(WealthOrderORM,cmd.operation_id)).details['actual']==fill
        await bank.settle(db,cmd.operation_id);assert (await db.get(WealthOrderORM,cmd.operation_id)).details['actual']==fill
        assert (await bank.latest_nav(db,lot['version_id'],datetime(2026,10,16,tzinfo=timezone.utc))).price=='1.20000000'
        _,service=provider.bind(db,'U1',http_bank)
        next_plan=await service.create('U1',PortfolioCreate(context_id=ctx['context_id']))
        assert next_plan.basis_fen==after['asset_summary']['total_assets_fen']>prior_plan.basis_fen
        from backend.app.models.wealth import WealthProductVersionORM
        from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioRecommendation
        from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
        version=await db.get(WealthProductVersionORM,lot['version_id']);version.rules={**version.rules,'purchase_paused':True};await db.commit()
        _,service=provider.bind(db,'U1',http_bank)
        group=await service.recommend('U1',PortfolioRecommendation(context_id=ctx['context_id'],filter=CandidateFilter(mode='general')))
        assert not group.items # frozen materials cannot override a live product stop


@pytest.mark.asyncio
async def test_real_observation_enters_bank_valuation_when_evidence_supplied(store,monkeypatch):
    import os
    filename=os.getenv('WEALTH_REAL_QUOTE_EVIDENCE')
    if not filename:pytest.skip('No actual exact-code observation supplied; never substitute a fixture')
    actual=json.loads(Path(filename).read_text(encoding='utf8'))
    assert actual['ok'] and actual['source']=='akshare_exact_code_unit_nav' and actual['code']=='000003'
    async def observed(code):assert code==actual['code'];return actual
    monkeypatch.setattr(quotes,'observation',observed)
    async with store() as db:
        ctx,c,cmd=await setup(db);aid=await authorize(db,cmd);await bank.accept_many(db,'U1',[(cmd,aid)]);await db.commit()
        c=await db.get(WealthContextORM,ctx['context_id']);c.data={**c.data,'business_at':'2026-10-16T06:00:00+00:00'};await db.commit()
        done=await bank.settle(db,cmd.operation_id);assert done['status']=='SETTLED';await db.commit()
        facts=await bank.assets(db,'U1');lot=facts['lots'][0]
        assert lot['nav_date']=='2026-10-14' and lot['nav']=='0.94700000'
        assert done['details']['source_nav_date']=='2026-10-13'
        assert lot['market_value_fen'] and facts['asset_summary']['valuation_complete']

@pytest.mark.asyncio
async def test_MW002_existing_thirty_day_rule_full_purchase_exit(store):
    from tests.integration.test_wealth_bank import command,authorize as native_authorize,move
    async with store() as db:
        await assess(db);cmd=command('mw002-native-purchase',product='MW002')
        aid=await native_authorize(db,cmd);await bank.accept_many(db,'U1',[(cmd,aid)])
        await move(db,12);done=await bank.settle(db,cmd.operation_id);assert done['status']=='SETTLED'
        facts=await bank.assets(db,'U1');assert facts['lots'][0]['locked_units']!='0.0000'
        sell=command('mw002-native-redeem',product='MW002',direction='redeem',amount=0,quantity='10.0000')
        with pytest.raises(BankError):await bank.precheck(db,'U1',sell)
        await move(db,44);preview=await bank.precheck(db,'U1',sell);assert preview['estimated_nav'] is not None
        aid=await native_authorize(db,sell);await bank.accept_many(db,'U1',[(sell,aid)])
        await move(db,47);pending=await bank.settle(db,sell.operation_id);assert pending['status']=='PENDING_CASH'
        balance=(await db.get(AccountORM,'A1')).balance_fen
        await move(db,49);done=await bank.settle(db,sell.operation_id);assert done['status']=='SETTLED'
        assert (await db.get(AccountORM,'A1')).balance_fen==balance+done['details']['actual']['cash_fen']
        assert (await bank.settle(db,sell.operation_id))==done
