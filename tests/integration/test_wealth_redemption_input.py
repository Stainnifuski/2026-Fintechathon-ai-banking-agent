"""B4 conversion through real internal HTTP, isolated holdings and cash."""
from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy import select, update, delete, func
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthLotORM, WealthNavORM, WealthUnitHoldORM, WealthAuthorizationORM
from backend.app.middleware.auth import get_current_user,create_access_token
from tests.integration.test_wealth_bank import store,http_store,assess,authorize,command,move
from backend.app.services.bank import wealth_service as bank


async def setup_holding(store):
    async with store() as db:
        await assess(db)
        await db.execute(update(WealthNavORM).where(WealthNavORM.version_id=='MF001:1').values(price='2.00000000'))
        await db.commit()
        cmd=command('redemption-input-opening',product='MF001',amount=200_000)
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        await move(db,12)
        await bank.settle(db,cmd.operation_id)


@pytest.mark.asyncio
async def test_historical_lot_after_catalogue_upgrade_uses_current_preview(store,http_store):
    from copy import deepcopy
    from backend.app.models.wealth import WealthProductORM,WealthProductVersionORM
    await setup_holding(store)
    async with store() as db:
        old=await db.get(WealthProductVersionORM,'MF001:1')
        original_rules=deepcopy(old.rules)
        db.add(WealthProductVersionORM(version_id='MF001:2',product_id='MF001',version=2,name=old.name,
            kind=old.kind,category=old.category,risk_level=old.risk_level,rules=deepcopy(old.rules),
            disclosure={**old.disclosure,'version':2},created_at=old.created_at))
        (await db.get(WealthProductORM,'MF001')).current_version=2
        price=await bank.latest_nav(db,'MF001:1',await bank.business_now(db,'A1'))
        db.add(WealthNavORM(nav_id='MF001:2:'+price.nav_date.isoformat(),version_id='MF001:2',nav_date=price.nav_date,price=price.price,available_at=price.available_at))
        await db.commit()
    client,_,_=http_store
    response=await client.post('/api/v1/wealth/redemptions/preview',json=request('units',units='100.0000'))
    assert response.status_code==200,response.text
    assert response.json()['preview']['command']['product_version']==2
    async with store() as db:
        lot=await db.scalar(select(WealthLotORM));assert lot.version_id=='MF001:1' and lot.cost_fen==200000
        assert (await db.get(WealthProductVersionORM,'MF001:1')).rules==original_rules
        assert (await db.get(AccountORM,'A1')).balance_fen==1800000


def request(mode='market_value',**fields):
    return dict(context_id='legacy-v1',position_key='A1:MF001:1',input_mode=mode,**fields)


@pytest.mark.asyncio
async def test_f06_f07_three_expressions_same_preview_no_holds(store,http_store):
    await setup_holding(store)
    client,_,_=http_store
    results=[]
    for payload in [request(market_value_fen=100_000),request('units',units='500.0000'),request('holding_ratio',holding_ratio='0.5')]:
        result=await client.post('/api/v1/wealth/redemptions/preview',json=payload)
        assert result.status_code==200,result.text
        dto=result.json();results.append(dto)
        assert dto['resolved_units']=='500.0000'
        assert dto['holding_ratio']=='0.50000000'
        assert dto['estimated_market_value_fen']==100_000
        assert '不代表实际到账金额' in dto['notice']
        assert dto['preview']['command']['amount_fen']==0 and dto['preview']['command']['direction']=='redeem'
        assert dto['preview']['authorization_status']=='AWAITING_CONFIRMATION'
        assert dto['estimated_net_cash_fen']<100_000
    small=await client.post('/api/v1/wealth/redemptions/preview',json=request('units',units='200.0000'))
    assert small.json()['estimated_market_value_fen']==40_000
    assert small.json()['holding_ratio']=='0.20000000'
    async with store() as db:
        assert (await db.get(AccountORM,'A1')).balance_fen==1_800_000
        assert (await db.scalar(select(WealthLotORM))).units_scaled==10_000_000
        assert await db.scalar(select(func.count()).select_from(WealthUnitHoldORM))==0
        assert (await client.post(f"/api/v1/wealth/previews/{results[0]['preview']['authorization_id']}/execute",json={})).status_code==403


@pytest.mark.asyncio
async def test_four_decimal_floor_maximum_missing_nav_and_scope(store,http_store):
    await setup_holding(store)
    client,_,app=http_store
    for payload,status in [
        (request(market_value_fen=200_001),409),
        (request('holding_ratio',holding_ratio='1.00000001'),422),
        (request('units',units='0.00001'),422),
        (request(market_value_fen=100,units='1'),422),
        ({**request(market_value_fen=100_000),'position_key':'A2:MF001:1'},404),
        ({**request(market_value_fen=100_000),'context_id':'other-scope'},409),
        ({**request(market_value_fen=100_000),'user_id':'U2'},422),
        ({**request(market_value_fen=100_000),'redemption_mode':'quick'},409),
    ]:
        response=await client.post('/api/v1/wealth/redemptions/preview',json=payload)
        assert response.status_code==status,response.text
    async with store() as db:
        await db.execute(update(WealthNavORM).where(WealthNavORM.version_id=='MF001:1').values(price='3.00000000'))
        await db.commit()
    value=(await client.post('/api/v1/wealth/redemptions/preview',json=request(market_value_fen=100_000))).json()
    assert value['resolved_units']=='333.3333'
    assert Decimal(value['resolved_units'])*Decimal(value['nav'])<=Decimal('1000')
    tiny=await client.post('/api/v1/wealth/redemptions/preview',json=request('holding_ratio',holding_ratio='0.00000001'))
    assert tiny.status_code==422
    app.dependency_overrides.pop(get_current_user)
    client.headers['Authorization']='Bearer '+create_access_token({'sub':'U2'})
    assert (await client.post('/api/v1/wealth/redemptions/preview',json=request(market_value_fen=100_000))).status_code==404
    client.headers['Authorization']='Bearer '+create_access_token({'sub':'U1'})
    async with store() as db:
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id=='MF001:1'))
        await db.commit()
    assert (await client.post('/api/v1/wealth/redemptions/preview',json=request(market_value_fen=100_000))).status_code==409


@pytest.mark.asyncio
async def test_locked_and_pending_units_do_not_expand_limit(store,http_store):
    await setup_holding(store)
    client,_,_=http_store
    async with store() as db:
        sell=command('pending-hold-for-input',product='MF001',direction='redeem',amount=0,quantity='600.0000')
        await bank.accept_many(db,'U1',[(sell,await authorize(db,sell))])
    result=await client.post('/api/v1/wealth/redemptions/preview',json=request('holding_ratio',holding_ratio='1'))
    assert result.status_code==409
    allowed=await client.post('/api/v1/wealth/redemptions/preview',json=request(market_value_fen=80_000))
    assert allowed.status_code==200,allowed.text
    assert allowed.json()['redeemable_units']=='400.0000'
    async with store() as db:
        lot=await db.scalar(select(WealthLotORM));lot.unlock_date=date(2027,1,1)
        await db.commit()
    assert (await client.post('/api/v1/wealth/redemptions/preview',json=request('units',units='1'))).status_code==409


@pytest.mark.asyncio
async def test_quick_conversion_keeps_quote_and_personal_verification(store,http_store):
    from backend.app.services.bank import wealth_research as research
    from backend.app.services.bank.wealth_preload import bank_products
    from backend.app.models.wealth_research import WealthContextORM
    from backend.app.schemas.wealth import InvestmentCommand
    from tests.integration.wealth_mfa_helpers import sms_verify
    client,_,_=http_store
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'redemption-input',
                 'mode':'snapshot','products':bank_products()}
        for product in payload['products']:product['rules']['dataset_version']='redemption-input'
        await research.import_dataset(db,payload)
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:redemption-input')
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-29T14:00:00+08:00'}
        await db.commit();await research.select_context(db,'U1',ctx.context_id,1);await assess(db)
        buy=InvestmentCommand(operation_id='input-quick-opening',direction='purchase',account_id=ctx.account_id,
            product_id='B017',product_version=1,disclosure_version=1,amount_fen=2_000_000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        ctx.data={**ctx.data,'business_at':'2026-09-30T10:00:00+08:00'};await db.commit()
        assert (await bank.settle(db,buy.operation_id))['status']=='SETTLED'
    response=await client.post('/api/v1/wealth/redemptions/preview',json={
        'context_id':context['context_id'],'position_key':f'{buy.account_id}:B017:1',
        'input_mode':'units','units':'1000.0000','redemption_mode':'quick'})
    assert response.status_code==200,response.text
    view=response.json()['preview'];base='/api/v1/wealth/previews/'+view['authorization_id']
    assert view['command']['command_version']==2
    assert view['command']['redemption_mode']=='quick' and view['command']['nav_quote_id']
    session=(await client.post('/api/v1/wealth/sessions',json={})).json()
    linked=await client.post('/api/v1/wealth/sessions/'+session['session_id']+'/preview-link',
                            json={'authorization_id':view['authorization_id']})
    assert linked.status_code==200,linked.text
    assert (await client.post(base+'/execute',json={})).status_code==403
    assert (await client.post(base+'/confirm',json={'digest':view['digest'],'disclosure_version':1,'accepted':True})).status_code==200
    assert (await sms_verify(client,base)).status_code==200
    executed=await client.post(base+'/execute',json={})
    assert executed.status_code==200,executed.text
    assert executed.json()['status']=='SETTLED'
