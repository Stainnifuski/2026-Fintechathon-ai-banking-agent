"""B6: recorded A contracts -> structured actions -> real isolated bank ledger.

No runtime provider registration, production assessment, demo seeding or LLM calls.
Public confirmation/MFA is exercised; settlement advances only the test clock.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest
from sqlalchemy import select, func

from backend.app.models import TransactionORM
from backend.app.models.wealth import WealthVerificationORM
from backend.app.schemas.bills import BillQuery
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.bill_service import analyze, transactions
from tests.integration.test_wealth_bank import store, http_store, move, NOW
from tests.integration.test_wealth_portfolio_prepare import RecordedSource, SEVEN, setup, confirm_and_verify
from tests.integration.wealth_mfa_helpers import sms_verify


class SharedScopeSource(RecordedSource):
    async def snapshot(self, db, user_id, context_id, bank_api):
        snapshot = await super().snapshot(db, user_id, context_id, bank_api)
        summary = (await bank_api.assets(user_id))['asset_summary']
        snapshot.update(asset_scope_id=summary['asset_scope_id'], account_ids=summary['account_ids'])
        snapshot.pop('snapshot_id', None)
        snapshot['snapshot_id'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        return snapshot


class Actions:
    def __init__(self, client):
        self.client, self.session, self.number = client, {}, 0

    async def __call__(self, name, params, selection=None):
        self.number += 1
        body = dict(schema_version='wealth-action-v1', request_id=f'b6-chain-{self.number:04}',
                    context_id='legacy-v1', action=name, params=params, **self.session)
        if selection:
            body['selection_context'] = selection
        response = await self.client.post('/api/v1/wealth/actions', json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'ok', result
        self.session = dict(session_id=result['session_id'], expected_session_version=result['session_version'])
        return result['data']


def consumer_evidence(value):
    """Remove authorization references even though every account is disposable."""
    if isinstance(value, dict):
        return {key:consumer_evidence(item) for key,item in value.items()
                if key not in {'authorization_id','authorization_ids','proof','challenge_id','verification_reference_id','totp_secret','token'}}
    if isinstance(value, list):
        return [consumer_evidence(item) for item in value]
    return value


async def register_recorded_handlers(source, local, app):
    """Explicit seven targets, not a replacement allocation/recommendation algorithm."""
    await source.record(local, SEVEN)
    catalog = {p['product_id']: p for p in await local.products('U1')}
    groups = []
    for bucket in ('cash_management', 'stable', 'growth'):
        items = []
        for pid, recorded_bucket, _ in SEVEN:
            if recorded_bucket != bucket:
                continue
            product = catalog[pid]
            unavailable = dict(metric='nav_change', status='unavailable', value_percent=None, reason='测试替身未录制历史指标')
            items.append(dict(family_id=pid, name=product['name'], bucket=bucket, reason='B6 同契约固定候选，不是模型建议',
                reason_codes=['fixture'], evidence=[], share_classes=[dict(product_id=pid, product_version=1, disclosure_version=1,
                share_class=None, product_risk_level=product['product_risk_level'], status=product['status'], tradable=product['status']=='on_sale',
                min_amount_fen=product['rules']['min_amount_fen'],
                fees=dict(purchase_fee_rate=product['rules'].get('purchase_fee_rate'), sales_service_fee_rate=None, redemption_fee_rules='按模拟银行规则'),
                performance=dict(one_year={**unavailable, 'start_date':None, 'end_date':None, 'source':None},
                                 latest_day={**unavailable, 'nav_date':None, 'previous_nav_date':None}),
                detail_ref=f'{pid}:1', data_source='isolated_fixture', data_as_of=NOW.isoformat())]))
        groups.append(dict(bucket=bucket, status='ok', filter={'mode':'general'}, items=items, next_cursor=None, risk_explanation=None))
    source.plan.update(as_of=NOW.isoformat(), basis_kind='linked_bank_assets', provider_id='b6-recorded', provider_version='v1',
                       candidate_groups=groups, reasons=['隔离同契约替身'], data_versions={'fixture':'b6-v1'})
    for position in source.plan['selected_positions']:
        position['current_fen'] = 0
    seen = []
    async def plan(**kwargs):
        assert kwargs['user_id'] == 'U1' and kwargs['context_id'] == 'legacy-v1'
        return deepcopy(source.plan)
    async def recommend(**kwargs):
        filter_ = kwargs['params']['filter']; seen.append(filter_)
        # Record the exact filter, while only replaying a declared fixture response.
        group = deepcopy(groups[1] if filter_['mode']=='category' else groups[2] if filter_['mode']=='theme' else groups[0])
        group['filter'] = filter_
        return group
    async def revise(**kwargs):
        selection = kwargs['params']['selection']
        assert selection['expected_version'] == source.plan['version']
        assert {(p['product_id'],p['target_ratio_total']) for p in selection['items']} == {(pid,ratio) for pid,_,ratio in SEVEN}
        source.plan['version'] += 1
        return deepcopy(source.plan)
    app.state.wealth_action_handlers = dict(plan_portfolio=plan, recommend_products=recommend, revise_portfolio=revise)
    app.state.wealth_portfolio_source = source
    return seen


async def single_confirm_verify_execute(client, view):
    base = '/api/v1/wealth/previews/' + view['authorization_id']
    assert (await client.post(base+'/execute', json={})).status_code == 403
    confirm = await client.post(base+'/confirm', json=dict(digest=view['digest'], disclosure_version=1, accepted=True))
    assert confirm.status_code == 200, confirm.text
    # A plan has already consumed the current TOTP step. Each later trade uses
    # its own bound SMS challenge, rather than weakening replay protection.
    verify = await sms_verify(client, base)
    assert verify.status_code == 200, verify.text
    execute = await client.post(base+'/execute', json={})
    assert execute.status_code == 200, execute.text
    repeated = await client.post(base+'/execute', json={})
    assert repeated.status_code == 200 and repeated.json()['operation_id'] == execute.json()['operation_id']
    return execute.json()


@pytest.mark.asyncio
async def test_b6_shortcuts_to_group_mfa_cancel_settle_returns_and_two_redemption_inputs(store, http_store, monkeypatch, tmp_path):
    client, local, _, secret = await setup(store, http_store, monkeypatch)
    from backend.app.services.ai import wealth_model, wealth_dialogue
    async def forbidden(*args, **kwargs):
        pytest.fail('B6 manual chain must not call NLP')
    monkeypatch.setattr(wealth_model, 'understand', forbidden)
    monkeypatch.setattr(wealth_model, 'explain', forbidden)
    monkeypatch.setattr(wealth_dialogue, 'message', forbidden)
    source = SharedScopeSource(client)
    seen = await register_recorded_handlers(source, local, http_store[2])
    actions = Actions(client)
    before = (await client.get('/api/v1/wealth/asset-summary')).json()
    snapshot = (await client.get('/api/v1/wealth/portfolio/snapshot')).json()
    assert snapshot['asset_scope_id'] == before['asset_scope_id']
    assert snapshot['basis_fen'] == before['total_assets_fen']
    plan = await actions('plan_portfolio', {})
    candidate_responses = []
    for filter_ in [dict(mode='general'), dict(mode='category',category='bond_fund'), dict(mode='theme',theme_key='high_dividend')]:
        group = await actions('recommend_products', {'filter':filter_})
        candidate_responses.append(group)
        assert {key:value for key,value in group['filter'].items() if value is not None} == filter_ and group['items']
    assert len(seen) == 3
    selection = dict(portfolio_id=plan['portfolio_id'], snapshot_id=plan['snapshot_id'], context_id='legacy-v1',
                     expected_version=plan['version'], items=[{k:p[k] for k in ('product_id','product_version','share_class','target_ratio_total')} for p in plan['selected_positions']])
    refs = {'object_type':'products','ids':[f"{p['product_id']}:1" for p in plan['selected_positions']]}
    revised = await actions('revise_portfolio', {'selection':selection}, refs)
    mapping = await actions('prepare_purchase', dict(portfolio_id=revised['portfolio_id'],expected_version=revised['version'],snapshot_id=revised['snapshot_id']))
    assert len(mapping['actions']) == 7
    assert (await client.get('/api/v1/wealth/assets')).json()['orders'] == []
    assert (await client.get('/api/v1/wealth/asset-summary')).json()['held_fen'] == 0
    base, views = await confirm_and_verify(client, mapping, secret)
    accepted = await client.post(base+'/execute',json={'version':mapping['execution_plan_version']})
    assert accepted.status_code == 200, accepted.text
    orders = [i['bank_result'] for i in accepted.json()['items']]
    assert len(orders)==7 and all(o['status']=='ACCEPTED' for o in orders)
    held = (await client.get('/api/v1/wealth/asset-summary')).json()
    assert held['held_fen'] == before['available_cash_fen'] and held['available_cash_fen']==0
    assert held['total_assets_fen']==before['total_assets_fen']
    retry = (await client.post(base+'/execute',json={'version':mapping['execution_plan_version']})).json()
    assert [i['bank_result']['operation_id'] for i in retry['items']] == [o['operation_id'] for o in orders]
    table = await actions('list_orders', {'page':1,'page_size':5})
    assert len(table['rows'])==5 and len(table['table_data']['rows'])==7
    cancelled = next(o for o in orders if o['version_id']=='MF005:1')
    eligibility = await actions('preview_order_cancellation', {'operation_ids':[cancelled['operation_id']]},
                                {'object_type':'orders','ids':[cancelled['operation_id']]})
    assert eligibility['items'][0]['eligibility']=='ALLOWED'
    assert (await client.post('/api/v1/wealth/orders/'+cancelled['operation_id']+'/cancel',
                             json=dict(digest=cancelled['digest'],disclosure_version=1,accepted=True))).status_code==200
    released = (await client.get('/api/v1/wealth/asset-summary')).json()
    assert released['cash_balance_fen']==before['cash_balance_fen']
    assert released['available_cash_fen']==cancelled['amount_fen']
    async with store() as db:
        await move(db,20)
        for order in orders:
            if order['operation_id']!=cancelled['operation_id']:
                assert (await bank.settle(db,order['operation_id']))['status']=='SETTLED'
    positions = await actions('list_positions', {'page':1,'page_size':5})
    assert len(positions['table_data']['rows'])==6
    assert positions['table_data']['scope_key']==before['asset_scope_id']
    summary = (await client.get('/api/v1/wealth/asset-summary')).json()
    assert summary['held_fen']==0
    assert summary['total_assets_fen']==summary['cash_balance_fen']+summary['positions_market_value_fen']+summary['redemption_receivable_fen']
    date = (await local.assets('U1'))['business_at'][:10]
    returns_responses = []
    for period in ('day','month','year','cumulative'):
        for scope in ('all','product'):
            result = await actions('show_wealth_returns', dict(scope=scope,period=period,**({'product_id':'MW001'} if scope=='product' else {}),**({'anchor_date':date} if period!='cumulative' else {})))
            assert result['asset_scope_id']==before['asset_scope_id']
            assert result['formula_version']=='actual-settlement-replay-v1'
            assert result['data_source']=='simulated_bank_actual_settlements'
            returns_responses.append(result)
    async with store() as db:
        ledger_count = await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.type=='investment_purchase'))
        report = await analyze(db,'U1',BillQuery(period='month',date=date[:7]))
        listing = await transactions(db,'U1',BillQuery(period='month',date=date[:7]))
        assert ledger_count==6 and report['totals']['count']==0 and listing['total']==0
        assert await db.scalar(select(func.count()).select_from(WealthVerificationORM))==len(views['items'])
    redemptions = []
    for index,redemption in enumerate([{'input_mode':'units','units':'10.0000'}, {'input_mode':'market_value','market_value_fen':1000}]):
        key = 'A1:MW001:1'
        converted = await actions('preview_redemption', {'redemption':{'context_id':'legacy-v1',**redemption}},
                                  {'object_type':'positions','ids':[key]})
        assert converted['preview']['command']['amount_fen']==0
        assert Decimal(converted['resolved_units'])>0 and '不代表实际到账金额' in converted['notice']
        redemptions.append({key:converted[key] for key in ('resolved_units','estimated_market_value_fen','holding_ratio','notice')})
        sale = await single_confirm_verify_execute(client, converted['preview'])
        pending = await actions('list_positions', {'page':1,'page_size':5})
        position = next(p for p in pending['table_data']['rows'] if p['position_key']==key)
        assert Decimal(position['held_units'])==Decimal(converted['resolved_units'])
        async with store() as db:
            await move(db,23+index*4)
            assert (await bank.settle(db,sale['operation_id']))['status'] in {'PENDING_CASH','SETTLED'}
            await move(db,25+index*4)
            assert (await bank.settle(db,sale['operation_id']))['status']=='SETTLED'
    final = await actions('list_orders', {'page':1,'page_size':5})
    assert final['total']==9
    assert sum(o['status']=='CANCELLED' for o in final['table_data']['rows'])==1
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.type=='investment_redemption'))==2
    # Disposable read-only consumer evidence. No secrets, OTPs, authorization IDs,
    # credentials or live-user records; frontend checks this actual API transcript.
    (tmp_path/'b6-evidence.json').write_text(json.dumps(consumer_evidence(dict(
        source='isolated_bank_http',context_id='legacy-v1',snapshot=snapshot,plan=revised,
        candidate_responses=candidate_responses,orders=final['table_data'],positions=positions['table_data'],
        before=before,held=held,released=released,settled=summary,returns=returns_responses,redemptions=redemptions,
        final_summary=(await client.get('/api/v1/wealth/asset-summary')).json())),ensure_ascii=False,indent=2),encoding='utf-8')
