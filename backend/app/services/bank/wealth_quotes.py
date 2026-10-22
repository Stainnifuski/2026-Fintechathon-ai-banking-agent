"""Bounded exact-code public NAV observations. Never creates cash or rewrites fills.

The fixed product/rule registry is independent of these market observations.
Publication time is unknown upstream: use actual observation time conservatively,
while retaining the source NAV date. No scheduled or all-market collection.
"""
import asyncio
import hashlib
import json
import time
import logging
from datetime import date, datetime, timezone
from sqlalchemy import select
from ...models.wealth import WealthNavORM, WealthProductVersionORM
from ...domain.wealth_paper_policy import PRICING_MODE

_cache = {}
POLICY_VERSION='published-nav-instant-20261017-v2'
_locks = {}
_limit = asyncio.Semaphore(3)
stats = {'http_code_queries': 0, 'cache_hits': 0, 'all_market_queries': 0}
SUCCESS_TTL=60
FAILURE_TTL=3
RETRYABLE_ERRORS={'PROVIDER_TIMEOUT','Timeout','ReadTimeout','ConnectTimeout','ConnectionError','ProxyError','SSLError','JSONDecodeError','ChunkedEncodingError','RemoteDisconnected'}


def required_trade_nav_date(at, calendar_version):
    """Latest completed NAV day under the project's 18:00 review cutoff.

    Weekends/holidays never advance this requirement. This is an execution
    freshness check, not an assertion about an issuer's publication deadline.
    """
    from datetime import timedelta, time as day_time
    from ...domain.wealth import TZ, business_day
    local = at.astimezone(TZ)
    day = local.date()
    if local.time() < day_time(18):
        day -= timedelta(days=1)
    while not business_day(day, calendar_version):
        day -= timedelta(days=1)
    return day


def require_trade_nav(version, price, at, observation_result=None):
    """A frozen offline anchor is not indefinitely valid for a new fill."""
    if version.rules.get('pricing_mode') != PRICING_MODE:
        return
    required = required_trade_nav_date(at, version.rules['calendar_version'])
    if price is None or price.nav_date < required:
        from .transfer_service import BankError
        known = price.nav_date.isoformat() if price else '缺失'
        detail=''
        if observation_result and not observation_result.get('ok'):
            reason=observation_result.get('error')
            detail=('本次联网查询超时' if reason in {'PROVIDER_TIMEOUT','Timeout','ReadTimeout','ConnectTimeout'} else '本次联网净值查询失败')
            if observation_result.get('attempts',1)>1:detail+='，已自动重试'
            detail+='。请重试本次核对；'
        raise BankError(f'{version.product_id}当前可用净值日期为{known}，本次交易需核对至{required.isoformat()}。'
            '最新有效净值尚未取得，不能使用过旧离线报价成交。'+detail+'也可移除此产品并选择净值已更新的产品；已有持仓和资金保留。',
            'NAV_UPDATE_REQUIRED', 409)

async def refresh_scope(db, user_id, codes=None, account_id=None):
    from . import wealth_service as bank, wealth_research as research
    from ...models.wealth import WealthLotORM, WealthOrderORM
    from ...models.wealth_research import WealthContextORM
    context = await research.selected(db, user_id)
    if account_id:
        from ...models.account import AccountORM
        account = await db.get(AccountORM, account_id)
        if not account or account.user_id != user_id: return {}
    at = await bank.business_now(db, account_id or (context.account_id if context else None))
    # No caller may ask the provider about arbitrary out-of-scope identities.
    if codes is not None:
        rows = await bank.products(db, user_id, facts_only=True, product_ids=sorted(set(codes)))
        versions = [f"{r['product_id']}:{r['version']}" for r in rows]
    else:
        peers = list(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id == user_id)))
        accounts = {p.account_id for p in peers if context and p.data.get('run_id', p.context_id) == context.data.get('run_id', context.context_id)}
        if account_id: accounts = {account_id}
        lots = select(WealthLotORM.version_id).where(WealthLotORM.user_id == user_id, WealthLotORM.units_scaled > 0)
        orders = select(WealthOrderORM.version_id).where(WealthOrderORM.user_id == user_id, WealthOrderORM.status.in_(['ACCEPTED','PENDING_PRICING']))
        if context or account_id:
            lots = lots.where(WealthLotORM.account_id.in_(accounts)); orders = orders.where(WealthOrderORM.account_id.in_(accounts))
        versions = set(await db.scalars(lots)) | set(await db.scalars(orders))
    async def observation_cutoff():
        return await bank.business_now(db, account_id or (context.account_id if context else None))
    result = await refresh(db, versions, at, subject_user_id=user_id, observation_cutoff=observation_cutoff)
    db.info['wealth_quote_updates']=result
    await db.commit() # only quote observations, before the coherent read snapshot
    import logging
    logging.getLogger(__name__).info('On-demand NAV: outcomes=%s; provider_calls=%d; cache_hits=%d; all_market_calls=0',result,stats['http_code_queries'],stats['cache_hits'])
    return result

async def observation(code):
    from .wealth_provider import AKShareProvider, normalize_history
    async with _locks.setdefault(code, asyncio.Lock()):
        cached = _cache.get(code)
        if cached and time.monotonic() - cached[0] < (SUCCESS_TTL if cached[1].get('ok') else FAILURE_TTL):
            stats['cache_hits'] += 1
            return cached[1]
        async with _limit:
            for attempt in range(2):
                stats['http_code_queries'] += 1
                try:
                    raw = await asyncio.to_thread(AKShareProvider(timeout=8, retries=1).fetch,
                        'fund_open_fund_info_em', symbol=code, indicator='单位净值走势')
                except Exception as error:
                    raw={'ok':False,'error_type':type(error).__name__}
                if raw.get('ok') or raw.get('error_type') not in RETRYABLE_ERRORS:break
            attempts=attempt+1
        try:
            if not raw.get('ok'): raise ValueError(raw.get('error_type', 'QUOTE_UNAVAILABLE'))
            points = normalize_history(raw['records'], raw['finished_at'])
            if not points: raise ValueError('QUOTE_EMPTY')
            result = {'ok': True, 'code': code, 'source': 'akshare_exact_code_unit_nav',
                'observed_at': raw['finished_at'], 'points': points,
                'latest_nav_date':points[-1]['date'],'attempts':attempts,
                'response_sha256': hashlib.sha256(json.dumps(raw['records'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        except (ValueError, KeyError, TypeError, ArithmeticError) as error:
            result = {'ok': False, 'code': code, 'error': str(error),'attempts':attempts}
            logging.getLogger(__name__).warning('NAV observation failed: code=%s; reason=%s; attempts=%d',code,result['error'],attempts)
        expired=[key for key,value in _cache.items() if time.monotonic()-value[0]>=(SUCCESS_TTL if value[1].get('ok') else FAILURE_TTL)]
        for key in expired:_cache.pop(key,None)
        if code not in _cache and len(_cache)>=128:_cache.pop(min(_cache,key=lambda k:_cache[k][0]),None)
        _cache[code] = (time.monotonic(), result)
        return result

async def refresh(db, version_ids, at, *, subject_user_id='public-market-data', observation_cutoff=None):
    """Only caller's scoped holdings/selection/order versions. Existing NAV immutable.

    Read-only transactions may consume previously committed observations without
    writing. A refresh is never allowed to mutate costs, units, fills or balances.
    """
    versions = list(await db.scalars(select(WealthProductVersionORM).where(
        WealthProductVersionORM.version_id.in_(set(version_ids))))) if version_ids else []
    real = [v for v in versions if v.rules.get('pricing_mode') == PRICING_MODE]
    results = await asyncio.gather(*(observation(code) for code in sorted({v.product_id for v in real})))
    # A live account must include observations completed by this read. A fixed
    # historical business clock still returns its historical cutoff unchanged.
    if observation_cutoff is not None:
        at = await observation_cutoff()
    by_code = {r['code']: r for r in results}
    for version in real:
        result = by_code[version.product_id]
        if not result['ok']: continue
        # Historical business clocks cannot consume observations obtained later.
        if datetime.fromisoformat(result['observed_at']) > at: continue
        points = [p for p in result['points'] if date.fromisoformat(p['date']) <= at.date()]
        from ...models.wealth import WealthOrderORM
        required=set(await db.scalars(select(WealthOrderORM.pricing_date).where(
            WealthOrderORM.version_id==version.version_id,WealthOrderORM.status.in_(['ACCEPTED','PENDING_PRICING']))))
        newest=max((p['date'] for p in points),default=None)
        points=[p for p in points if p['date']==newest or date.fromisoformat(p['date']) in required]
        existing = set(await db.scalars(select(WealthNavORM.nav_date).where(WealthNavORM.version_id == version.version_id)))
        for point in points:
            day = date.fromisoformat(point['date'])
            if day in existing: continue # preserve original published observations/fills
            from sqlalchemy.dialects import postgresql, sqlite
            insert = postgresql.insert if db.get_bind().dialect.name=='postgresql' else sqlite.insert
            await db.execute(insert(WealthNavORM).values(nav_id=f'{version.version_id}:{day}', version_id=version.version_id,
                nav_date=day, price=point['nav'], available_at=datetime.fromisoformat(point['available_at'])).on_conflict_do_nothing())
            from .audit_service import record_bank_event
            from uuid import uuid4
            record_bank_event(db,event_key=uuid4().hex,subject_user_id=subject_user_id,
                action='public_nav_observation',result='observed',facts={'product_id':version.product_id,
                    'version_id':version.version_id,'nav_date':point['date'],'value':point['nav'],
                    'observed_at':result['observed_at'],'source':result['source'],
                    'response_sha256':result['response_sha256']},decision_chain=['EXACT_CODE','NO_LEDGER_MUTATION'])
        if points: await db.flush()
    return {r['code']: {k: v for k, v in r.items() if k != 'points'} for r in results}
