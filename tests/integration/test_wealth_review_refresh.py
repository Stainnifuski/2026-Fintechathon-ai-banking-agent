"""Synthetic isolated SQLite/PG review changes; never uses the user's ledger."""
from datetime import timedelta
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, http_store, NOW
from tests.integration.test_wealth_target_purchase import selected, confirm_verify
from backend.app.models.wealth import (WealthNavORM, WealthProductVersionORM,
    WealthAuthorizationORM, WealthOrderORM, WealthCashHoldORM)
from backend.app.services.bank import wealth_quotes as quotes
from backend.app.services.bank import wealth_service as bank
from backend.app.domain.wealth_paper_policy import PRICING_MODE
from backend.app.integrations.wealth_bank_api import HttpWealthBankApi


async def change_synthetic_quote(store):
    async with store() as db:
        nav = await bank.latest_nav(db, 'MF001:1', NOW)
        # Only the fictional fixture is changed to simulate a newer quote binding.
        nav.price = '1.23456789'
        await db.commit()


@pytest.mark.asyncio
async def test_first_review_automatically_rebinds_unseen_draft(store, http_store, monkeypatch):
    payload = await selected(store, monkeypatch)
    client, _, _ = http_store
    original = HttpWealthBankApi.precheck
    calls = 0
    async def advancing(self, user, command):
        nonlocal calls
        calls += 1
        if calls == 3:
            await change_synthetic_quote(store)
        return await original(self, user, command)
    monkeypatch.setattr(HttpWealthBankApi, 'precheck', advancing)
    result = await client.post('/api/v1/wealth/target-purchases/prepare', json=payload)
    assert result.status_code == 200, result.text
    assert str(result.json()['preview']['items'][0]['estimated_nav']) == '1.23456789'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0


@pytest.mark.asyncio
async def test_changed_visible_quote_renews_review_but_not_old_consent(store, http_store, monkeypatch):
    payload = await selected(store, monkeypatch)
    client, _, _ = http_store
    first = await client.post('/api/v1/wealth/target-purchases/prepare', json=payload)
    assert first.status_code == 200, first.text
    data = first.json(); base = '/api/v1/wealth/plans/'+data['plan']['plan_id']
    await change_synthetic_quote(store)
    old_items = [{'authorization_id': i['authorization_id'], 'digest': i['digest'],
        'disclosure_version': i['command']['disclosure_version'], 'accepted': True} for i in data['preview']['items']]
    failed = await client.post(base+'/confirm', json={'version': 1, 'items': old_items})
    assert failed.status_code == 409 and failed.json()['detail']['code'] == 'QUOTE_CHANGED'
    renewed = await client.post(base+'/refresh-review', json={'version': 1})
    assert renewed.status_code == 200, renewed.text
    fresh = renewed.json()
    assert fresh['plan']['version'] == 2
    assert fresh['preview']['verification_reference_id'] != data['preview']['verification_reference_id']
    assert {i['command']['amount_fen'] for i in fresh['preview']['items']} == {100000}
    assert (await client.post(base+'/confirm', json={'version': 1, 'items': old_items})).status_code == 409
    assert (await client.post(base+'/execute', json={'version': 2})).status_code == 409
    async with store() as db:
        for item in data['preview']['items']:
            assert (await db.get(WealthAuthorizationORM, item['authorization_id'])).status == 'EXPIRED'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
    await confirm_verify(client, fresh)
    executed = await client.post(base+'/execute', json={'version': 2})
    assert executed.status_code == 200 and executed.json()['status'] == 'COMPLETED', executed.text
    assert (await client.post(base+'/refresh-review', json={'version': 2})).status_code == 409


@pytest.mark.asyncio
async def test_observation_cutoff_moves_only_with_business_clock(store, monkeypatch):
    start = NOW + timedelta(days=2000)
    end = start + timedelta(seconds=3)
    observed = start + timedelta(seconds=2)
    async def observation(code):
        return {'ok': True, 'code': code, 'source': 'SYNTHETIC_TEST',
            'response_sha256': 'f'*64, 'observed_at': observed.isoformat(),
            'points': [{'date': start.date().isoformat(), 'nav': '1.12345678',
                'available_at': observed.isoformat()}]}
    monkeypatch.setattr(quotes, 'observation', observation)
    async with store() as db:
        product = await db.get(WealthProductVersionORM, 'MF001:1')
        product.rules = {**product.rules, 'pricing_mode': PRICING_MODE}
        await db.commit()
        await quotes.refresh(db, ['MF001:1'], start)
        assert await db.get(WealthNavORM, f'MF001:1:{start.date()}') is None
        async def live_cutoff(): return end
        await quotes.refresh(db, ['MF001:1'], start, observation_cutoff=live_cutoff)
        nav = await db.get(WealthNavORM, f'MF001:1:{start.date()}')
        assert nav and nav.price == '1.12345678'


@pytest.mark.asyncio
async def test_retry_expired_review_automatically_renews_same_request(store, http_store, monkeypatch):
    from datetime import datetime, timezone
    payload = await selected(store, monkeypatch)
    client, _, _ = http_store
    url = '/api/v1/wealth/target-purchases/prepare'
    first = await client.post(url, json=payload)
    assert first.status_code == 200, first.text
    async with store() as db:
        for item in first.json()['preview']['items']:
            auth = await db.get(WealthAuthorizationORM, item['authorization_id'])
            auth.expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        await db.commit()
    retry = await client.post(url, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()['plan']['plan_id'] == first.json()['plan']['plan_id']
    assert retry.json()['plan']['version'] == 2
    assert all(i['authorization_status'] == 'AWAITING_CONFIRMATION' for i in retry.json()['preview']['items'])


@pytest.mark.asyncio
async def test_refresh_shortfall_keeps_original_review_and_does_not_submit(store, http_store, monkeypatch):
    from backend.app.models import AccountORM
    payload = await selected(store, monkeypatch)
    client, _, _ = http_store
    first = await client.post('/api/v1/wealth/target-purchases/prepare', json=payload)
    assert first.status_code == 200, first.text
    async with store() as db:
        (await db.get(AccountORM, 'A1')).balance_fen = 150000
        await db.commit()
    base = '/api/v1/wealth/plans/'+first.json()['plan']['plan_id']
    result = await client.post(base+'/refresh-review', json={'version': 1})
    assert result.status_code == 409 and '还差500.00元' in result.json()['detail']['message']
    progress = await client.get(base+'/progress')
    assert progress.json()['version'] == 1
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0
