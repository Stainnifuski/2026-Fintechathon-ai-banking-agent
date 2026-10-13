"""Batch catalogue must preserve publication visibility and current-state reads."""
from datetime import timedelta
import pytest
from sqlalchemy import select,delete,event
from backend.app.models.wealth import WealthNavORM,WealthProductORM
from backend.app.services.bank import wealth_service as bank
from tests.integration.test_wealth_bank import store,NOW

@pytest.mark.asyncio
async def test_batch_nav_visibility_matches_single_read_and_not_cached(store):
    async with store() as db:
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id=='MF002:1'))
        db.add_all([
            WealthNavORM(nav_id='prior',version_id='MF002:1',nav_date=NOW.date()-timedelta(days=1),price='1.1000',available_at=None),
            WealthNavORM(nav_id='explicit-future',version_id='MF002:1',nav_date=NOW.date(),price='9.9999',available_at=NOW+timedelta(hours=1))])
        await db.commit()
        queries=[]
        engine=db.get_bind()
        def count(conn,cursor,statement,parameters,context,executemany):queries.append(statement)
        event.listen(engine,'before_cursor_execute',count)
        try:catalog=await bank.products(db,'U1')
        finally:event.remove(engine,'before_cursor_execute',count)
        row=next(r for r in catalog if r['product_id']=='MF002')
        single=await bank.latest_nav(db,'MF002:1',NOW)
        assert row['nav']==single.price=='1.1000'
        nav_queries=[q for q in queries if 'wealth_navs' in q]
        assert len(nav_queries)==1 # directory size does not multiply NAV queries
        future=await db.get(WealthNavORM,'explicit-future');future.available_at=NOW-timedelta(seconds=1)
        product=await db.get(WealthProductORM,'MF002');product.status='suspended';await db.commit()
        row=next(r for r in await bank.products(db,'U1') if r['product_id']=='MF002')
        assert row['nav']=='9.9999' and row['status']=='suspended'

@pytest.mark.asyncio
async def test_legacy_same_day_price_not_visible_before_18_beijing(store):
    async with store() as db:
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id=='MF002:1'))
        db.add(WealthNavORM(nav_id='unpublished-legacy',version_id='MF002:1',nav_date=NOW.date(),price='2.0000',available_at=None))
        await db.commit()
        row=next(r for r in await bank.products(db,'U1') if r['product_id']=='MF002')
        assert row['nav'] is None and await bank.latest_nav(db,'MF002:1',NOW) is None
