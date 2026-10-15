"""Full directory and bounded, deduplicated evidence caches owned by bank 4."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, func, or_, update
from ...models.wealth_research import WealthFundDirectoryORM as Directory, WealthFundCacheORM as Cache
from .wealth_provider import normalize_history, AKShareProvider, SOURCE_URL

TTL = {'history': 86400, 'status': 21600, 'fees': 604800}
LIMIT = asyncio.Semaphore(2)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


async def import_directory(db, raw):
    if not raw.get('ok'): return {'ok': False, 'error_code': raw.get('error_type', 'SOURCE_UNAVAILABLE')}
    rows = [r for r in raw['records'] if '货币' not in str(r.get('基金类型',''))]; codes = set()
    # Validate the complete source before changing the retained directory.
    for r in rows:
        code = str(r.get('基金代码', ''))
        if not re.fullmatch(r'\d{6}', code) or code in codes or not r.get('基金简称') or '基金类型' not in r:
            raise ValueError('DIRECTORY_SCHEMA_DRIFT')
        codes.add(code)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    for start in range(0, len(rows), 100):
        values = [{'code': r['基金代码'], 'name': r['基金简称'], 'fund_type': r['基金类型'] or '未知',
                   'metadata_json': {'source': 'akshare', 'source_url': SOURCE_URL, 'provider_version': raw['provider_version'],
                                     'available_at': raw['finished_at'], 'published_at': None,
                                     'quality_flags': ['fund_type_unknown'] if not r['基金类型'] else []}} for r in rows[start:start+100]]
        statement = insert(Directory).values(values)
        await db.execute(statement.on_conflict_do_update(index_elements=['code'], set_={
            'name': statement.excluded.name, 'fund_type': statement.excluded.fund_type, 'metadata_json': statement.excluded.metadata_json}))
    # Missing from newest directory is not proof of termination: retain with stale evidence date.
    await db.commit()
    return {'ok': True, 'source_count': len(rows), 'retained_count': await db.scalar(select(func.count()).select_from(Directory))}


async def search(db, query='', fund_type=None, page=1, page_size=20):
    query = query.strip()
    conditions = []
    if query:
        # Escape LIKE metacharacters; directory search never executes model code.
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        category = re.fullmatch(r'(?:找|查找|搜索|查询)?\s*(?:几只|一些|全部|所有)?\s*(债券|货币|股票|混合|指数)(?:型)?(?:基金)?', query)
        matches = [Directory.code.like(pattern, escape='\\'), Directory.name.ilike(pattern, escape='\\')]
        # Generic categories are directory facets, not substrings of fund names.
        # Named funds and codes keep literal matching, including LIKE escaping.
        if category:
            matches.append(Directory.fund_type.ilike('%' + category.group(1) + '%'))
        conditions.append(or_(*matches))
    if fund_type: conditions.append(Directory.fund_type == fund_type)
    total = await db.scalar(select(func.count()).select_from(Directory).where(*conditions))
    rows = (await db.scalars(select(Directory).where(*conditions).order_by(Directory.code).offset((page-1)*page_size).limit(page_size))).all()
    return {'total': total, 'page': page, 'page_size': page_size, 'items': [
        {'code': r.code, 'product_id': 'R'+r.code, 'name': r.name, 'fund_type': r.fund_type, **r.metadata_json,
         'identity': '真实基金数据', 'trade_eligible': False, 'missing_rules': ['reviewed_risk', 'full_rules']} for r in rows]}


async def claim(db, code, data_type, force=False):
    if not re.fullmatch(r'\d{6}', code) or data_type not in TTL: raise ValueError('INVALID_FUND_REQUEST')
    directory = await db.get(Directory, code)
    if not directory: raise ValueError('FUND_NOT_IN_DIRECTORY')
    now = datetime.now(timezone.utc); key = code+':'+data_type
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    created = await db.execute(insert(Cache).values(cache_key=key, code=code, data_type=data_type, data={}, status='FETCHING',
        expires_at=now, lease_until=now+timedelta(minutes=30)).on_conflict_do_nothing(index_elements=['cache_key']))
    acquired = created.rowcount == 1
    if not acquired:
        conditions = [Cache.cache_key == key, Cache.lease_until <= now]
        if not force: conditions.append(Cache.expires_at <= now)
        result = await db.execute(update(Cache).where(*conditions).values(status='FETCHING', lease_until=now+timedelta(minutes=30), error_code=None))
        acquired = result.rowcount == 1
    await db.commit()
    return acquired


def fetch_evidence(provider, code, data_type):
    if data_type == 'history': raw = provider.fetch('fund_open_fund_info_em', symbol=code, indicator='单位净值走势')
    elif data_type == 'status': raw = provider.fetch('fund_fee_em', symbol=code, indicator='交易状态')
    else:
        # Fee, amount and settlement fields remain individually auditable. Partial success is retained.
        reports = [provider.fetch('fund_fee_em', symbol=code, indicator=i) for i in ('运作费用', '申购费率（前端）', '赎回费率', '申购与赎回金额', '交易确认日')]
        return {'source_product_code': code, 'rules_complete': False, 'fields': {i: r for i, r in zip(
            ('annual_fees', 'purchase_fees', 'redemption_fees', 'amount_limits', 'confirmation'), reports)},
            'partial': any(not r.get('ok') for r in reports), 'source_url': f'https://fundf10.eastmoney.com/jjfl_{code}.html',
            'available_at': datetime.now(timezone.utc).isoformat(), 'published_at': None}
    if not raw.get('ok'): raise ValueError(raw.get('error_type', 'SOURCE_UNAVAILABLE'))
    result = {'source_product_code': code, 'source': 'akshare', 'provider_version': provider.version,
        'source_url': f'https://fundf10.eastmoney.com/{"jjfl" if data_type=="status" else "jjjz"}_{code}.html',
        'available_at': raw['finished_at'], 'published_at': None, 'rules_complete': False, 'data_type': data_type}
    if data_type == 'history': result['observations'] = normalize_history(raw['records'], raw['finished_at'])
    else: result.update(records=raw['records'], columns=raw['columns'])
    return result


async def refresh(sessions, code, data_type, provider=None):
    async with LIMIT:
        try:
            evidence = await asyncio.to_thread(fetch_evidence, provider or AKShareProvider(timeout=90, retries=2), code, data_type)
            error = None
        except Exception as exc:
            evidence = None; error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        async with sessions() as db:
            row = await db.get(Cache, code+':'+data_type)
            if row is None: return
            now = datetime.now(timezone.utc)
            if evidence:
                row.data = evidence; row.fetched_at = now; row.expires_at = now+timedelta(seconds=TTL[data_type])
                row.status = 'PARTIAL' if evidence.get('partial') else 'READY'; row.error_code = 'PARTIAL_SOURCE_FAILURE' if evidence.get('partial') else None
            else:
                row.status = 'FAILED'; row.error_code = error[:80]; row.expires_at = now+timedelta(minutes=5)
            row.lease_until = now
            await db.commit()


async def detail(db, code):
    identity = await db.get(Directory, code)
    if identity is None: raise ValueError('FUND_NOT_IN_DIRECTORY')
    rows = (await db.scalars(select(Cache).where(Cache.code == code))).all(); now = datetime.now(timezone.utc)
    return {'code': code, 'product_id': 'R'+code, 'name': identity.name, 'fund_type': identity.fund_type,
        'identity': '真实基金数据', 'trade_eligible': False, 'missing_rules': ['reviewed_risk', 'full_rules'],
        'evidence': {r.data_type: {'status': r.status, 'stale': bool(r.data) and (r.status == 'FAILED' or utc(r.expires_at) <= now), 'error_code': r.error_code,
            'fetched_at': utc(r.fetched_at).isoformat() if r.fetched_at else None, 'data': r.data} for r in rows}}
