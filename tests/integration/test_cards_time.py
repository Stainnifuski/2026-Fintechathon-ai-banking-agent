"""最新main卡片响应兼容SQLite UTC及偏移瞬时；展示由共享北京工具负责。"""
from datetime import datetime, timedelta
import pytest
from tests.integration.test_cards import db, client, preview, headers
from backend.app.models import CardORM
from backend.app.schemas.cards import CardView
from backend.app.services.bank.card_service import card_view
from backend.app.models import AccountORM


@pytest.mark.asyncio
async def test_card_confirmation_expiry_keeps_utc_after_database_reload(client, db):
    response = await preview(client, 'card-utc-preview', action='card_report_lost', card_id='C1')
    assert response.status_code == 201
    instant = datetime.fromisoformat(response.json()['expires_at'].replace('Z', '+00:00'))
    assert instant.utcoffset() == timedelta(0)
    db.expire_all()
    response = await client.get('/api/v1/cards/operations/card-utc-preview',
                                headers=headers())
    assert response.status_code == 200
    restored = datetime.fromisoformat(response.json()['expires_at'].replace('Z', '+00:00'))
    assert restored.utcoffset() == timedelta(0) and restored == instant


@pytest.mark.asyncio
@pytest.mark.parametrize('stamp', ['2026-10-03T15:59:59', '2026-10-03T15:59:59+00:00', '2026-10-03T23:59:59+08:00'])
async def test_card_lost_event_is_same_utc_instant(db, stamp):
    card = await db.get(CardORM, 'C1')
    card.lost_reported_at = datetime.fromisoformat(stamp)
    account = await db.get(AccountORM, 'A1')
    dto = CardView.model_validate(card_view(card, account))
    assert dto.model_dump(mode='json')['lost_reported_at'] == '2026-10-03T15:59:59Z'
