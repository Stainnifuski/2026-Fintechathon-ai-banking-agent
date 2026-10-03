"""Supplement contract regression, synthetic source fixtures only."""
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, http_store
from backend.app.services.bank import wealth_fund_data as funds


def directory():
    return {'ok': True, 'provider_version': '1.19.1', 'finished_at': '2026-10-02T00:00:00+00:00',
        'records': [{'基金代码': f'{i:06}', '基金简称': f'契约样本{i}', '基金类型': '货币型' if i == 9 else '' if i == 3 else '债券型'} for i in range(1, 27)]}


@pytest.mark.asyncio
async def test_directory_full_pagination_unknown_idempotency_and_injection(store, http_store):
    async with store() as db:
        assert (await funds.import_directory(db, directory()))['source_count'] == 26
        assert (await funds.import_directory(db, directory()))['retained_count'] == 26
        assert (await funds.search(db, page=2, page_size=20))['items'][0]['code'] == '000021'
        assert (await funds.search(db, '000003'))['items'][0]['fund_type'] == '未知'
        assert (await funds.search(db, '%'))['total'] == 0
        assert (await funds.search(db, '债券基金'))['total'] == 24
        assert (await funds.search(db, '找几只货币型基金'))['items'][0]['code'] == '000009'
        assert (await funds.search(db, '契约样本26'))['total'] == 1
        assert (await funds.search(db, '不存在债券基金'))['total'] == 0
        before = await db.scalar(select(func.count()).select_from(funds.Directory))
        malformed = directory(); malformed['records'].append(malformed['records'][0])
        with pytest.raises(ValueError): await funds.import_directory(db, malformed)
        assert await db.scalar(select(func.count()).select_from(funds.Directory)) == before
    client, _, _ = http_store
    result = await client.post('/api/v1/wealth/funds/search', json={'query': '000003'})
    assert result.status_code == 200 and result.json()['items'][0]['trade_eligible'] is False
    assert (await client.post('/api/v1/wealth/funds/search', json={'subject_user_id': 'U2'})).status_code == 422
    assert (await client.post('/api/v1/wealth/funds/detail', json={'code': '../env'})).status_code == 422


class Provider:
    version = 'contract-fake'
    def __init__(self, fail=False): self.fail = fail; self.calls = 0
    def fetch(self, method, **kwargs):
        self.calls += 1
        if self.fail: return {'ok': False, 'error_type': 'PROVIDER_TIMEOUT'}
        return {'ok': True, 'columns': ['净值日期', '单位净值'], 'finished_at': '2026-10-02T00:00:00+00:00',
            'records': [{'净值日期': '2026-09-30', '单位净值': '1.2345'}]}


@pytest.mark.asyncio
async def test_mixed_pool_immutable_import_default_and_source_disclosure(store, tmp_path):
    import json
    from shutil import copyfile
    from backend.app.services.bank.wealth_preload import CONFIG, compose
    from backend.app.services.bank.wealth_research import import_dataset, default_dataset_key, catalog
    from backend.app.services.bank.wealth_knowledge import seed_mixed
    copyfile(CONFIG/'bank_rules.json', tmp_path/'bank_rules.json')
    (tmp_path/'preload.json').write_text(json.dumps({'version':'test','target':{'real_funds':1,'simulated_bank_wealth':16},
        'funds':[{'code':'000001','expected_name':'契约样本1','category':'bond'}]}),encoding='utf-8')
    async with store() as db:
        await funds.import_directory(db, directory())
        assert await funds.claim(db,'000001','history')
    await funds.refresh(store,'000001','history',Provider())
    async with store() as db:
        payload=await compose(db,tmp_path)
        first=await import_dataset(db,payload)
        from backend.app.services.bank.wealth_preload import bank_products
        assert first['ok'] and first['written']==len(bank_products())+1
        await seed_mixed(db,payload); await db.commit()
        repeat=await compose(db,tmp_path)
        assert (await import_dataset(db,repeat))['duplicate']
        assert await default_dataset_key(db)==first['dataset_key']
        rows=await catalog(db)
        from backend.app.services.bank.wealth_preload import bank_products
        expected = len(bank_products())
        assert len(rows)==expected+1 and sum(p['trade_eligible'] for p in rows)==expected
        assert next(p for p in rows if p['source']=='akshare')['risk_level'] is None
        bank=next(p for p in rows if p['source']=='synthetic')
        assert bank['source_rules'] and bank['official_reference']['sha256']
        assert bank['rules_adaptations'] and bank['fund_family_id'].startswith('BANK-TEMPLATE-')
        from tests.integration.test_wealth_bank import assess, command
        from backend.app.services.bank import wealth_research as research, wealth_service as service
        from backend.app.models.wealth_research import WealthContextORM
        from backend.app.services.bank.transfer_service import BankError
        ctx=await research.initialize(db,'U1',dataset_key=first['dataset_key'])
        await db.commit(); await research.select_context(db,'U1',ctx['context_id'],1)
        await assess(db)
        context=await db.get(WealthContextORM,ctx['context_id'])
        context.data={**context.data,'business_at':'2026-10-09T10:00:00+08:00'}
        cmd=command('closed-after-offer',product='B015',amount=10000000).model_copy(update={'account_id':ctx['account_id']})
        # Closed maturity/offer guards run before a quote is returned.
        from backend.app.models.account import AccountORM
        account=await db.get(AccountORM,ctx['account_id']); account.balance_fen=20000000
        with pytest.raises(BankError) as error: await service.precheck(db,'U1',cmd)
        assert error.value.code=='PRODUCT_UNAVAILABLE'


@pytest.mark.asyncio
async def test_cache_single_claim_retained_failure_and_type_separation(store):
    async with store() as db:
        await funds.import_directory(db, directory())
        assert await funds.claim(db, '000001', 'history')
        assert not await funds.claim(db, '000001', 'history')
        with pytest.raises(ValueError): await funds.claim(db, '000009', 'history')
        with pytest.raises(ValueError): await funds.claim(db, '000001', 'money_yield')
    provider = Provider(); await funds.refresh(store, '000001', 'history', provider)
    async with store() as db:
        assert not await funds.claim(db, '000001', 'history')
        first = (await funds.detail(db, '000001'))['evidence']['history']['data']
        assert first['observations'][0]['nav'] == '1.23450000'
        assert await funds.claim(db, '000001', 'history', force=True)
    await funds.refresh(store, '000001', 'history', Provider(fail=True))
    async with store() as db:
        result = (await funds.detail(db, '000001'))['evidence']['history']
        assert result['status'] == 'FAILED' and result['error_code'] == 'PROVIDER_TIMEOUT'
        assert result['data'] == first and not result['data'].get('simulated')
        assert await db.scalar(select(func.count()).select_from(funds.Cache)) == 1


def test_money_yield_units_unknown_and_date():
    result = funds.normalize_money([{'净值日期': '2026-09-30', '每万份收益': '-0.12', '7日年化收益率': None}], '2026-10-02T00:00:00+00:00')
    assert result[0]['income_per_10000'] == '-0.12'
    assert result[0]['seven_day_annualized_percent'] is None and 'nav' not in result[0]
    with pytest.raises(ValueError): funds.normalize_money([{'净值日期': '2027-01-01', '每万份收益': '0.1', '7日年化收益率': '1'}], '2026-10-02T00:00:00+00:00')
