"""Model-extracted search terms remain candidate searches, never product selections."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func

from backend.app.models.wealth import WealthOrderORM
from backend.app.models.wealth import WealthConversationORM
from backend.app.services.ai import wealth_agent as agent
from backend.app.services.ai import task_presentation
from backend.app.routers.nova import WealthViewContext, wealth_card_view
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.domain.wealth_portfolio_candidates import ProductIndex, pipeline
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool, NOW
from tests.integration.test_nova_wealth import conversation, context, add_assessment
from tests.integration.test_wealth_bank import store


@pytest.fixture
def bank(monkeypatch):
    bank = SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'}),
        call=AsyncMock(side_effect=AssertionError('a search must not resolve an exact product or execute')))
    monkeypatch.setattr(agent, 'HttpWealthBankApi', lambda:bank)
    return bank


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['华夏', '华夏成长', '华夏 成长', '000001', '不存在的产品'])
async def test_new_chat_opens_name_search_without_picking_or_exact_resolution(store, bank, query):
    async with store() as db:
        await conversation(db)
        message = f'我要投资{query}'
        args = agent.WealthPlanningArguments(kind='recommend', name_query=query, name_query_quote=message)
        response = await agent.manage(args, replace(context(db), original_message=message))
        card = response['wealth_cards'][0]
        assert card['command']['kind'] == 'recommend'
        assert card['command']['name_query'] == query
        assert card['command']['products'] == []
        assert not card['command']['auto_prepare']
        assert 'selected' not in card.get('view_context', {}).get('completed_stages', [])
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        replay = await agent.manage(args, replace(context(db), original_message=message))
        assert replay['wealth_cards'] == response['wealth_cards']
        bank.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_name_query_survives_assessment_and_continues_same_card(store, bank):
    async with store() as db:
        await conversation(db, assessed=False)
        args = agent.WealthPlanningArguments(kind='recommend', name_query='华夏', name_query_quote='买华夏的基金')
        opened = await agent.manage(args, replace(context(db), original_message='买华夏的基金'))
        card = opened['wealth_cards'][0]
        assert card['assessment_pending'] and card['command']['name_query'] == '华夏'
        add_assessment(db)
        await db.commit()
        continued = await agent.manage(agent.WealthPlanningArguments(kind='recommend', task_id=card['task_id']),
            replace(context(db, 'after-assessment-search'), original_message='继续找候选'))
        assert len(continued['wealth_cards']) == 1
        assert continued['wealth_cards'][0]['command']['name_query'] == '华夏'
        assert continued['wealth_cards'][0]['command']['products'] == []
        assert not continued['wealth_cards'][0].get('assessment_pending')


@pytest.mark.asyncio
async def test_invented_search_is_dropped_without_inventing_a_product(store, bank):
    async with store() as db:
        await conversation(db)
        response = await agent.manage(agent.WealthPlanningArguments(kind='recommend',
            name_query='华夏', name_query_quote='买华夏'), replace(context(db), original_message='买嘉实'))
        assert response['wealth_cards'][0]['command']['name_query'] is None
        assert response['wealth_cards'][0]['command']['products'] == []
        assert '未能从本轮原文核实' in response['reply']
        bank.call.assert_not_awaited()


def test_existing_candidate_pipeline_searches_fragments_and_codes_with_risk_filtering():
    snapshot, facts = fixture_pool(3)
    index = ProductIndex(snapshot)
    by_name = pipeline(index, CandidateFilter(mode='name', name_query='红利'), facts, 'fixture-user', NOW)
    assert by_name.status == 'ok' and len(by_name.matched_codes) == 6
    assert len(by_name.family_candidates) == 3
    by_code = pipeline(index, CandidateFilter(mode='name', name_query='Q00A'), facts, 'fixture-user', NOW)
    assert by_code.matched_codes == ['Q00A']
    missing = pipeline(index, CandidateFilter(mode='name', name_query='不存在'), facts, 'fixture-user', NOW)
    assert missing.status == 'no_category_match' and missing.family_candidates == []
    snapshot, facts = fixture_pool(2, user_level=2, product_level=4)
    blocked = pipeline(ProductIndex(snapshot), CandidateFilter(mode='name', name_query='红利'), facts, 'fixture-user', NOW)
    assert blocked.status == 'risk_mismatch' and blocked.family_candidates == []


def test_shared_matcher_handles_multiple_terms_case_and_fullwidth_codes():
    snapshot, facts = fixture_pool(2)
    for product in snapshot.products[:2]:
        product.name = '虚构Growth成长混合'
    index = ProductIndex(snapshot)
    result = pipeline(index, CandidateFilter(mode='name', name_query='成长 growth'), facts, 'fixture-user', NOW)
    assert set(result.matched_codes) == {'Q00A', 'Q00C'}
    code = pipeline(index, CandidateFilter(mode='name', name_query='Ｑ００Ａ'), facts, 'fixture-user', NOW)
    assert code.matched_codes == ['Q00A']
    partial_code = pipeline(index, CandidateFilter(mode='name', name_query='Q00'), facts, 'fixture-user', NOW)
    assert partial_code.matched_codes == []


def test_model_extracted_name_terms_are_verified_individually_without_inventing_terms():
    args = agent.WealthPlanningArguments(kind='recommend',name_query='华夏 成长',
        name_query_quote='华夏的成长基金')
    assert agent.verified_name_query(args,'我要买华夏的成长基金') == '华夏 成长'
    args = args.model_copy(update={'name_query':'嘉实 成长'})
    assert agent.verified_name_query(args,'我要买华夏的成长基金') is None


@pytest.mark.asyncio
async def test_manual_handoff_keeps_search_and_user_selection_context(store, bank):
    async with store() as db:
        await conversation(db)
        ctx = replace(context(db), original_message='我要买华夏')
        opened = await agent.manage(agent.WealthPlanningArguments(kind='recommend',
            name_query='华夏',name_query_quote='买华夏'), ctx)
        card = opened['wealth_cards'][0]
        selected = {'product_id':'MF002','product_version':1,'name':'本人已选产品'}
        view = WealthViewContext(version=card['version'],search_mode='name',name_query='华夏 成长',
            search_request_id=card['request_id'],visible_products=[selected],selected_products=[selected])
        await wealth_card_view(ctx.session_id,card['task_id'],view,{'user_id':'U1'},db)
        released = await task_presentation.release(ctx,'wealth:'+card['task_id'],'manual','handoff-name-search')
        assert released['destination'] == 'wealth'
        row = await db.get(WealthConversationORM,card['task_id'],populate_existing=True)
        assert row.data['agent_released']
        assert row.data['command']['name_query'] == '华夏'
        assert row.data['view_context']['name_query'] == '华夏 成长'
        assert row.data['view_context']['search_mode'] == 'name'
        assert row.data['view_context']['selected_products'][0]['product_id'] == 'MF002'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
