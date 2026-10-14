"""Version-bound read-only table metadata; no cash mutations."""
from decimal import Decimal
from sqlalchemy import select
from ...models.wealth import WealthProductVersionORM, WealthNavORM


async def enrich(db, lots, orders, now):
    from .wealth_service import nav_available_at, TZ
    references = {row['version_id'] for row in [*lots, *orders]}
    if not references:
        return
    metadata = {}
    versions = list(await db.scalars(select(WealthProductVersionORM).where(WealthProductVersionORM.version_id.in_(references))))
    navs = list(await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id.in_(references),
        WealthNavORM.nav_date <= now.astimezone(TZ).date()).order_by(WealthNavORM.nav_date.desc())))
    for version in versions:
        published = [p for p in navs if p.version_id == version.version_id and nav_available_at(p) <= now]
        latest = published[0] if published else None
        previous = published[1] if len(published) > 1 else None
        applicable = not (version.category == 'cash' and version.kind == 'open_fund')
        status = 'ok' if applicable and previous else 'unavailable' if applicable else 'not_applicable'
        metadata[version.version_id] = {
            'product_id': version.product_id, 'product_version': version.version,
            'product_name': version.name, 'share_class': version.rules.get('share_class'),
            'disclosure_version': version.disclosure['version'],
            'daily_metric': {'status': status, 'label': '最新净值涨跌',
                'value_percent': format((Decimal(latest.price) / Decimal(previous.price) - 1) * 100, '.4f') if status == 'ok' else None,
                'date': latest.nav_date.isoformat() if latest else None,
                'previous_date': previous.nav_date.isoformat() if previous else None,
                'reason': None if status == 'ok' else '缺少两个已公布净值' if applicable else '此类产品不适用单位净值日涨跌',
                'source': 'simulated_bank_published_nav'},
        }
    for row in [*lots, *orders]:
        row.update(metadata.get(row['version_id'], {}))
