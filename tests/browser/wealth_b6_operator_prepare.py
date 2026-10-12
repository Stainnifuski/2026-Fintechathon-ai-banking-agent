"""Prepare an unsubmitted seven-action task for the isolated 8012 role UI check.

Uses normal login and public APIs. No token or authorization is printed or saved.
Requires the explicitly configured, fresh B6 operator server, not a demo service.
"""
import asyncio
import httpx


async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8012', timeout=30) as client:
        response = await client.post('/api/v1/auth/login', json={'phone':'13800000001','password':'password123'})
        response.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
        facts = (await client.get('/api/v1/wealth/assets')).json()
        assert facts['context_id'] == 'legacy-v1' and not facts['orders']
        async def action(name, params, suffix):
            response = await client.post('/api/v1/wealth/actions', json={
                'schema_version':'wealth-action-v1','request_id':f'b6-role-ui-{suffix}',
                'context_id':'legacy-v1','action':name,'params':params})
            response.raise_for_status()
            result = response.json()
            assert result['status'] == 'ok', result.get('message')
            return result['data']
        plan = await action('plan_portfolio', {}, 'plan')
        prepared = await action('prepare_purchase', {'portfolio_id':plan['portfolio_id'],
            'expected_version':plan['version'],'snapshot_id':plan['snapshot_id']}, 'prepare')
        assert len(prepared['actions']) == 7
        response = await client.get('/api/v1/wealth/tasks')
        response.raise_for_status()
        task = next(t for t in response.json()['items'] if t['plan_id'] == prepared['execution_plan_id'])
        response = await client.post(f"/api/v1/wealth/tasks/{task['task_id']}/handoff", json={
            'request_id':'b6-role-ui-handoff','expected_version':task['version']})
        response.raise_for_status()
        assert response.json()['control_mode'] == 'HANDOFF_PENDING'
        facts = (await client.get('/api/v1/wealth/assets')).json()
        assert not facts['orders'] and facts['asset_summary']['held_fen'] == 0
        print('Prepared seven-action HANDOFF_PENDING task; no orders or holds.')


if __name__ == '__main__':
    asyncio.run(main())
