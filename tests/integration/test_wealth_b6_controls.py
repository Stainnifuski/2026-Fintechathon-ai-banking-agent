"""B6 customer/operator recovery through public HTTP with actual JWT subjects."""
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, http_store, command, assess, authorize
from backend.app.middleware.auth import get_current_user, create_access_token
from backend.app.models.wealth import WealthAuthorizationORM, WealthOrderORM, WealthCashHoldORM
from backend.app.services.orchestration import wealth_task as tasks


@pytest.mark.asyncio
async def test_jwt_handoff_release_requires_fresh_personal_confirmation(store, http_store, monkeypatch):
    client, _, app = http_store
    from backend.app.services.ai import wealth_model, wealth_dialogue
    async def no_model(*args, **kwargs):
        raise AssertionError('B6 manual recovery must not call NLP or the model')
    monkeypatch.setattr(wealth_model, 'understand', no_model)
    monkeypatch.setattr(wealth_model, 'explain', no_model)
    monkeypatch.setattr(wealth_dialogue, 'message', no_model)
    async with store() as db:
        await assess(db)
        original = command('b6-controls-original')
        task = await tasks.create_command(db, 'U1', 'b6-controls-session', 'legacy-v1', original)
        old_auth = await authorize(db, original)

    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS', 'U2')
    def subject(uid):
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': uid})

    customer = f"/api/v1/wealth/tasks/{task['task_id']}"
    operator = f"/api/v1/wealth/operator/tasks/{task['task_id']}"
    subject('U1')
    response = await client.post(customer + '/handoff', json={'request_id': 'b6-controls-handoff', 'expected_version': 1})
    assert response.status_code == 200, response.text
    handed = response.json()
    assert handed['control_mode'] == 'HANDOFF_PENDING'
    assert (await client.post(operator + '/claim', json={'request_id': 'b6-controls-forged-role', 'expected_version': handed['version']})).status_code == 403
    query = await client.post('/api/v1/wealth/actions', json={
        'schema_version':'wealth-action-v1','request_id':'b6-controls-read-orders',
        'context_id':'legacy-v1','action':'list_orders','params':{'page':1,'page_size':5}})
    assert query.status_code == 200 and query.json()['status'] == 'ok', query.text
    assert (await client.post(customer + '/repreview', json={'request_id': 'b6-controls-blocked-preview', 'expected_version': handed['version']})).status_code == 409

    subject('U2')
    assert (await client.get(operator + '/context')).status_code == 403  # Not yet claimed.
    response = await client.post(operator + '/claim', json={'request_id': 'b6-controls-claim', 'expected_version': handed['version']})
    assert response.status_code == 200, response.text
    claimed = response.json()
    assert claimed['owner_id'] == 'U2' and claimed['control_mode'] == 'HUMAN'
    assert (await client.get(operator + '/context')).status_code == 200
    assert (await client.post(f'/api/v1/wealth/previews/{old_auth}/execute', json={})).status_code == 404
    response = await client.post(operator + '/release', json={'request_id': 'b6-controls-release', 'expected_version': claimed['version'], 'reason': '请本人重读资料并重新确认'})
    assert response.status_code == 200, response.text
    released = response.json()
    assert released['control_mode'] == 'AGENT' and released['status'] == 'PAUSED'
    assert (await client.get(operator + '/context')).status_code == 403

    subject('U1')
    blocked = await client.post(f'/api/v1/wealth/previews/{old_auth}/execute', json={})
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'AUTHORIZATION_EXPIRED'
    response = await client.post(customer + '/resume', json={'request_id': 'b6-controls-resume', 'expected_version': released['version']})
    assert response.status_code == 200, response.text
    resumed = response.json()
    stale = await client.post(f'/api/v1/wealth/previews/{old_auth}/execute', json={})
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'AUTHORIZATION_EXPIRED'
    response = await client.post(customer + '/repreview', json={'request_id': 'b6-controls-new-preview', 'expected_version': resumed['version']})
    assert response.status_code == 200, response.text
    fresh = response.json()['preview']
    assert fresh['authorization_status'] == 'AWAITING_CONFIRMATION'
    assert fresh['command']['operation_id'] != original.operation_id
    assert fresh['command']['amount_fen'] == original.amount_fen
    assert (await client.post(f"/api/v1/wealth/previews/{fresh['authorization_id']}/execute", json={})).status_code == 403
    async with store() as db:
        assert (await db.get(WealthAuthorizationORM, old_auth)).status == 'EXPIRED'
        assert (await db.get(WealthAuthorizationORM, fresh['authorization_id'])).confirmed_at is None
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0
