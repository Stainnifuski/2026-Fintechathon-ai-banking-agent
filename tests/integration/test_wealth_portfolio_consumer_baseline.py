"""B0/F13用现有模拟银行验证占款边界，不改变生产授权。"""
import pytest
from tests.integration.test_wealth_bank import store, http_store, assess, authorize, command
from backend.app.services.bank import wealth_service as bank


@pytest.mark.asyncio
async def test_f13_preview_accept_and_cancel_conserve_cash(store):
    async with store() as db:
        await assess(db)
        cmd = command('b0-f13-hold-boundary', amount=100000)
        before = (await bank.assets(db, 'U1'))['cash'][0]
        await bank.precheck(db, 'U1', cmd)
        authorization_id = await authorize(db, cmd)
        preview = (await bank.assets(db, 'U1'))['cash'][0]
        for field in ('balance_fen', 'available_fen', 'held_fen'):
            assert preview[field] == before[field]
        order = (await bank.accept_many(db, 'U1', [(cmd, authorization_id)]))[0]
        assert order['status'] == 'ACCEPTED'
        accepted = (await bank.assets(db, 'U1'))['cash'][0]
        assert accepted['balance_fen'] == before['balance_fen']
        assert accepted['available_fen'] == before['available_fen'] - 100000
        assert accepted['held_fen'] == before['held_fen'] + 100000
        # Eligibility and digest confirmation are verified by the existing HTTP regression.
        await bank.cancel(db, 'U1', cmd.operation_id)
        cancelled = (await bank.assets(db, 'U1'))['cash'][0]
        for field in ('balance_fen', 'available_fen', 'held_fen'):
            assert cancelled[field] == before[field]


@pytest.mark.asyncio
async def test_manual_catalog_preview_confirmation_mfa_submit_and_cancel_without_llm(store, http_store, monkeypatch):
    import pyotp
    from backend.app.models import UserORM
    from backend.app.services.ai.llm import LLMService

    async def forbid_llm(*args, **kwargs):
        pytest.fail('The manual bank flow must not call an LLM')

    monkeypatch.setattr(LLMService, '_generate', forbid_llm)
    client, _, _ = http_store
    secret = pyotp.random_base32()
    async with store() as db:
        await assess(db)
        user = await db.get(UserORM, 'U1')
        user.totp_secret, user.totp_enabled = secret, True
        await db.commit()
    catalog = await client.get('/api/v1/wealth/products')
    assert catalog.status_code == 200
    product = next(item for item in catalog.json() if item['product_id'] == 'MF002')
    payload = {
        'operation_id': 'b0-manual-http-purchase', 'direction': 'purchase',
        'product_id': product['product_id'], 'product_version': product['version'],
        'disclosure_version': product['disclosure']['version'],
        'account_id': 'A1', 'amount_fen': 100000, 'units': '0.0000',
    }
    before = (await client.get('/api/v1/wealth/assets')).json()['cash'][0]
    cross_owner = await client.post('/api/v1/wealth/previews', json={**payload, 'account_id': 'A2'})
    # The bank hides another owner's account as not found.
    assert cross_owner.status_code == 404
    response = await client.post('/api/v1/wealth/previews', json=payload)
    assert response.status_code == 200, response.text
    view = response.json()
    reference = f"/api/v1/wealth/previews/{view['authorization_id']}"
    assert view['authorization_status'] == 'AWAITING_CONFIRMATION'
    assert (await client.get('/api/v1/wealth/assets')).json()['cash'][0] == before
    assert (await client.post(reference + '/execute', json={})).status_code == 403
    confirmed = await client.post(reference + '/confirm', json={
        'digest': view['digest'], 'disclosure_version': payload['disclosure_version'], 'accepted': True,
    })
    assert confirmed.status_code == 200
    assert confirmed.json()['status'] == 'AWAITING_MFA'
    assert (await client.get('/api/v1/wealth/assets')).json()['cash'][0] == before
    assert (await client.post(reference + '/execute', json={})).status_code == 403
    verified = await client.post(reference + '/verify', json={'method': 'totp', 'proof': pyotp.TOTP(secret).now()})
    assert verified.status_code == 200
    accepted = await client.post(reference + '/execute', json={})
    assert accepted.status_code == 200, accepted.text
    order = accepted.json()
    assert order['status'] == 'ACCEPTED'
    after = (await client.get('/api/v1/wealth/assets')).json()['cash'][0]
    assert after['balance_fen'] == before['balance_fen']
    assert after['held_fen'] == before['held_fen'] + payload['amount_fen']
    assert after['available_fen'] == before['available_fen'] - payload['amount_fen']
    assert (await client.post(reference + '/execute', json={})).json() == order
    order_path = f"/api/v1/wealth/orders/{payload['operation_id']}"
    eligibility = await client.get(order_path + '/cancellation-eligibility')
    assert eligibility.json()['eligibility'] == 'ALLOWED'
    cancellation = await client.post(order_path + '/cancel', json={
        'digest': order['digest'], 'disclosure_version': payload['disclosure_version'], 'accepted': True,
    })
    assert cancellation.status_code == 200, cancellation.text
    assert cancellation.json()['status'] == 'CANCELLED'
    assert (await client.get('/api/v1/wealth/assets')).json()['cash'][0] == before
