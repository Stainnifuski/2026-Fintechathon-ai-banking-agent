"""NOVA uses the same scoped assets as both pages, without treating them as cash."""
import pytest
from sqlalchemy import delete

from backend.app.models.wealth import WealthNavORM
from backend.app.models import AccountORM
from backend.app.models.wealth_research import WealthContextORM, WealthContextSelectionORM, WealthDatasetORM
from backend.app.services.bank.nova_reads import financial_overview
from backend.app.services.ai.nova import offline_business
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from tests.integration.test_wealth_bank import store
from tests.integration.test_wealth_bank import NOW
from tests.integration.test_wealth_asset_summary import seed_f18, summary


@pytest.mark.asyncio
async def test_nova_total_matches_shared_summary_with_checking_holds_and_receivable(store):
    await seed_f18(store)
    expected = await summary(store)
    async with store() as db:
        result = await financial_overview(db, 'U1')
        actual = result['asset_summary']
        for field in ('account_ids', 'asset_scope_id', 'snapshot_id', 'cash_balance_fen',
                      'held_fen', 'available_cash_fen', 'positions_market_value_fen',
                      'redemption_receivable_fen', 'total_assets_fen'):
            assert actual[field] == expected[field]
        assert 'A3' in actual['account_ids'] and 'C1' not in actual['account_ids']
        assert actual['cash_balance_fen'] == 800_000
        assert actual['held_fen'] == 100_000
        assert actual['redemption_receivable_fen'] == 50_000
        assert actual['total_assets_fen'] == 1_000_000
        assert result['totals']['balance_fen'] != actual['total_assets_fen']
        reply = await offline_business('我的总资产是多少？', ToolContext('U1', db, PageContext(page='home')))
        assert '¥10,000.00' in reply['reply']
        assert '不等于可支付现金' in reply['reply']


@pytest.mark.asyncio
async def test_nova_missing_valuation_is_not_zero_assets(store):
    await seed_f18(store)
    async with store() as db:
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id == 'MF002:1'))
        await db.commit()
        result = await financial_overview(db, 'U1')
        assert result['asset_summary']['total_assets_fen'] is None
        assert result['asset_summary']['valuation_complete'] is False
        reply = await offline_business('总资产', ToolContext('U1', db, PageContext(page='wealth')))
        assert '暂不能完整计算' in reply['reply']


@pytest.mark.asyncio
async def test_nova_context_total_does_not_mix_ordinary_cash(store):
    async with store() as db:
        db.add(WealthDatasetORM(dataset_key='nova-mock', dataset_id='nova-mock', version='1', mode='mock', manifest={}, ingested_at=NOW))
        db.add(AccountORM(account_id='S1', user_id='U1', account_type='savings', card_number='s1', balance_fen=123_400))
        await db.flush()
        db.add(WealthContextORM(context_id='nova-context', user_id='U1', account_id='S1', dataset_key='nova-mock',
                               data={'disposable': True, 'run_id': 'nova-run', 'business_at': NOW.isoformat()}))
        db.add(WealthContextSelectionORM(user_id='U1', context_id='nova-context', version=1))
        await db.commit()
        result = await financial_overview(db, 'U1')
    expected = await summary(store)
    assert result['asset_summary']['snapshot_id'] == expected['snapshot_id']
    assert result['asset_summary']['account_ids'] == ['S1']
    assert result['asset_summary']['total_assets_fen'] == 123_400
    assert result['totals']['balance_fen'] == 2_000_000
    assert 'S1' not in {row['account_id'] for row in result['accounts']}
