"""受保护比赛银行只读事实；缺失全授权、版本或控制证据时关闭。"""
from copy import deepcopy
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import httpx
from ..schemas.wealth_portfolio_candidates import CurrentFacts, FormalAssessment, BankPurchaseFact,CurrentSimulationEvidence
from ..services.orchestration.wealth_portfolio import SnapshotError, build_snapshot
from ..services.orchestration.wealth_portfolio_candidates import CandidateError
from ..services.orchestration.wealth_portfolio_data import digest
from ..schemas.wealth_portfolio import AccountFact


class ReadTransport(Protocol):
    async def get(self, path: str) -> object: ...


class LocalBankReads:
    """只允许既有本地GET；身份由原银行校验，不签发令牌。"""
    PATHS = {'/api/v1/auth/me', '/api/v1/wealth/assets', '/api/v1/wealth/products',
             '/api/v1/wealth/assessments/current', '/api/v1/wealth/contexts', '/api/v1/wealth/tasks'}

    def __init__(self, base_url: str, token: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('loopback bank URL required')
        self.base_url, self.token = base_url.rstrip('/'), token
        self.requests = 0

    async def get(self, path: str) -> object:
        if path not in self.PATHS:
            raise ValueError('read path not allowed')
        self.requests += 1
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
                response = await client.get(self.base_url+path, headers={'Authorization': 'Bearer '+self.token})
        except httpx.RequestError as error:
            raise CandidateError('BANK_READ_UNAVAILABLE', 503) from error
        if response.status_code != 200:
            raise CandidateError('BANK_READ_HTTP_'+str(response.status_code), response.status_code,
                                 {'path': path})
        return response.json()


def business_content(value: object) -> object:
    """观察时刻不冒充业务版本；业务日期仍保留。"""
    if isinstance(value, list):
        return [business_content(v) for v in value]
    if isinstance(value, dict):
        return {k: (business_day(v)
                    if k == 'business_at' else business_content(v))
                for k, v in value.items() if k not in {'as_of', 'read_at'}}
    return value


def business_day(value: str) -> str:
    """业务时刻必须自带时区，不能借主机时区补齐。"""
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('bank business timestamp requires timezone')
    return parsed.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()


def snapshot_business_content(observed: dict) -> object:
    """DAG节点运行日志不是资产版本；保留任务版本、关联和全部控制事实。"""
    value=deepcopy(observed)
    for task in value.get('tasks',{}).get('items',[]):
        task.pop('nodes',None)
    return business_content(value)


def native_product_evidence(row: dict) -> dict:
    """映射既有模拟银行当前版本事实；不是下单资格或官方基金评级。

    完整性沿用 bank.wealth_service.product_version 的字段和日历检查。
    不推导销售有效期、不改研究版本、不将缺字段默认成许可。
    """
    from ..domain.wealth import CALENDAR_VERSION
    from ..domain.wealth_calendar import CALENDAR_VERSION as CN_CALENDAR_VERSION
    rules, disclosure = row.get('rules', {}), row.get('disclosure', {})
    required_rules = {'currency', 'min_amount_fen', 'max_amount_fen', 'purchase_fee_rate',
        'redeem_fee_bands', 'holding_days', 'confirm_business_days', 'cash_business_days',
        'cutoff', 'calendar_version', 'cancel_before_pricing', 'purchase_paused', 'redeem_paused'}
    required_disclosure = {'nature', 'manager', 'seller', 'risk', 'fees', 'liquidity',
        'holding_period_days', 'pricing', 'settlement', 'cancellation', 'limitations'}
    complete = (required_rules <= rules.keys() and required_disclosure <= disclosure.keys()
        and rules.get('calendar_version') in {CALENDAR_VERSION, 'synthetic-weekdays-v1', CN_CALENDAR_VERSION})
    # 原接口 status/open/stale 是服务端事实；空缺仍为未知。
    sale = None
    if (row.get('status') is not None and row.get('status') != 'on_sale'
            or rules.get('purchase_paused') is True or row.get('purchase_open') is False
            or row.get('data_stale') is True):
        sale = False
    elif (row.get('status') == 'on_sale' and rules.get('purchase_paused') is False
            and row.get('purchase_open') is True and row.get('data_stale') is False):
        sale = True
    level, version = row.get('product_risk_level'), row.get('version')
    risk_proven = type(level) is int and 1 <= level <= 5 and type(version) is int and version > 0
    return {'purchase_allowed': sale, 'rules_complete': complete,
        'risk_current_confirmed': risk_proven,
        'risk_evidence_id': 'bank-current-simulated:'+digest({'product_id': row['product_id'],
            'version': version, 'risk_level': level}) if risk_proven else None}


class BankPortfolioFacts:
    def __init__(self, transport: ReadTransport, data_version: str, daily_version: str) -> None:
        self.transport, self.data_version, self.daily_version = transport, data_version, daily_version
        self.observations: dict = {}

    async def _read_groups(self, user_id: str, groups: list[tuple[str, str]]) -> dict:
        identity = await self.transport.get('/api/v1/auth/me')
        if not isinstance(identity, dict) or identity.get('user_id') != user_id:
            raise CandidateError('FACT_SCOPE_FORBIDDEN', 403)
        result = {}
        for key, path in groups:
            result[key] = await self.transport.get('/api/v1/wealth/'+path)
        if (any(key in result and not isinstance(result[key], dict) for key in ('assets', 'contexts', 'tasks'))
                or ('tasks' in result and not isinstance(result['tasks'].get('items'), list))
                or ('products' in result and (not isinstance(result['products'], list)
                    or any(not isinstance(row, dict) or not row.get('product_id')
                        or not isinstance(row.get('rules', {}), dict)
                        or not isinstance(row.get('disclosure', {}), dict) for row in result['products'])))
                or ('assessment' in result and result['assessment'] is not None and not isinstance(result['assessment'], dict))):
            raise CandidateError('BANK_READ_SCHEMA_INVALID', 503)
        self.observations.update(deepcopy(result))
        return result

    async def observe(self, user_id: str) -> dict:
        return await self._read_groups(user_id, [('assets', 'assets'), ('assessment', 'assessments/current'),
                          ('products', 'products'), ('contexts', 'contexts'), ('tasks', 'tasks')])

    async def read(self, user_id: str) -> dict:
        try:
            first = await self.observe(user_id)
            second = await self.observe(user_id)
            if digest(snapshot_business_content(first)) != digest(snapshot_business_content(second)):
                raise SnapshotError('bank facts changed during read', 'BANK_FACTS_CHANGED', 409)
            assets = second['assets']
            # legacy cash/lots响应没有授权集合/共同版本，禁止改名为完整汇总。
            required = {'summary', 'accounts', 'lots', 'orders', 'basis_status', 'data_versions',
                        'assessment', 'assessment_provenance'}
            if not isinstance(assets, dict) or not required <= assets.keys():
                assets = self._adapt_run_assets(user_id, second)
            if assets['basis_status'] == 'fixture_only' or assets['summary'].get('data_source') == 'fixture_only':
                raise SnapshotError('fixture prohibited', 'FIXTURE_SOURCE_FORBIDDEN', 403)
            assessment = second['assessment']
            expected = {k: assessment[k] for k in ('assessment_id','capacity_level','purchase_allowed','expires_at','confirmed')} if assessment else None
            if assets['assessment'] != expected:
                raise SnapshotError('assessment binding unproven', 'ASSESSMENT_BINDING_UNPROVEN', 503)
            build_snapshot(assets)
            return deepcopy(assets)
        except CandidateError as error:
            raise SnapshotError(str(error), error.code, error.status_code) from error

    def _run_accounts(self, raw: dict, contexts: dict) -> tuple:
        """已核对的同run全集；只读范围证明与估值/写权限分别校验。"""
        context_id = raw.get('context_id')
        items = contexts.get('items', [])
        selected = [r for r in items if isinstance(r, dict) and r.get('context_id') == context_id]
        if (contexts.get('selected') != context_id or len(selected) != 1
                or selected[0].get('disposable') is not True or not selected[0].get('run_id')
                or selected[0].get('run_state') != 'ACTIVE' or raw.get('scope') != 'disposable_simulation'):
            raise SnapshotError('full authorized run scope unproven', 'ASSET_SUMMARY_UNAVAILABLE', 503)
        peers = [r for r in items if isinstance(r, dict) and r.get('disposable') is True
            and r.get('run_id', r.get('context_id')) == selected[0]['run_id']]
        expected_ids = [r.get('account_id') for r in peers]
        cash, lots, orders = raw.get('cash'), raw.get('lots'), raw.get('orders')
        if (not all(isinstance(v,list) for v in (cash,lots,orders))
                or any(r.get('run_state') != 'ACTIVE' for r in peers)
                or any(not isinstance(a,str) or not a for a in expected_ids)
                or len(set(expected_ids)) != len(expected_ids)
                or any(not isinstance(a,dict) for a in cash)
                or len(cash) != len(expected_ids) or {a.get('account_id') for a in cash} != set(expected_ids)):
            raise SnapshotError('full run account set inconsistent', 'ASSET_SCOPE_UNPROVEN', 503)
        accounts = [AccountFact.model_validate(a).model_dump() for a in cash if a.get('account_type') != 'credit']
        ids = sorted(a['account_id'] for a in accounts)
        if not ids:
            raise SnapshotError('no authorized debit accounts', 'ASSET_SCOPE_UNPROVEN', 503)
        return accounts, ids, expected_ids, context_id, selected[0]

    def _adapt_run_assets(self, user_id: str, observed: dict) -> dict:
        """同run全查询适配；稳定双读不声称事务锁，不覆盖未知普通范围。"""
        raw, contexts = observed['assets'], observed['contexts']
        accounts, ids, expected_ids, context_id, selected = self._run_accounts(raw, contexts)
        lots, orders = raw['lots'], raw['orders']
        if any(not isinstance(r,dict) or r.get('account_id') not in expected_ids for r in lots+orders):
            raise SnapshotError('asset row outside authorized scope', 'ASSET_SCOPE_FORBIDDEN', 403)
        lots = [r for r in lots if r['account_id'] in ids]
        orders = [r for r in orders if r['account_id'] in ids]
        position_value = None if any(r.get('market_value_fen') is None for r in lots) else sum(r['market_value_fen'] for r in lots)
        pending = [r for r in orders if r.get('direction') == 'redeem' and r.get('status') == 'PENDING_CASH']
        amounts = [r.get('details',{}).get('actual',{}).get('cash_fen') for r in pending]
        receivable = None if any(v is None for v in amounts) else sum(amounts)
        missing = (['positions.market_value_fen'] if position_value is None else []) + (['orders.redemption_receivable_fen'] if receivable is None else [])
        complete = not missing
        balances = sum(a['balance_fen'] for a in accounts)
        scope = 'bank-run:'+digest({'user_id':user_id,'run_id':selected['run_id'],'account_ids':ids})
        assessment = observed['assessment']
        assessment_view = {k:assessment[k] for k in ('assessment_id','capacity_level','purchase_allowed','expires_at','confirmed')} if assessment else None
        return {'summary':{'schema_version':'wealth-asset-summary-v1','asset_scope_id':scope,'account_ids':ids,
            'snapshot_id':'adapter:'+digest(snapshot_business_content(observed)), 'as_of':raw['as_of'],'business_at':raw['business_at'],
            'cash_balance_fen':balances,'held_fen':sum(a['held_fen'] for a in accounts),
            'available_cash_fen':sum(a['available_fen'] for a in accounts),'positions_market_value_fen':position_value,
            'redemption_receivable_fen':receivable,'total_assets_fen':balances+position_value+receivable if complete else None,
            'valuation_complete':complete,'missing_fields':missing,'data_source':'protected-local-bank'},
            'accounts':accounts,'lots':lots,'orders':orders,'products':observed['products'],
            'basis_status':'confirmed','context_id':context_id,'context_epoch':selected.get('context_epoch'),
            'assessment':assessment_view,'assessment_provenance':{'source':'protected-local-bank',
                'version':'adapter:'+digest(assessment) if assessment else None},
            'data_versions':{'data_version':self.data_version,'daily_version':self.daily_version,
                'offers_version':'adapter:'+digest(observed['products'])}}

    async def read_current(self, user_id: str, at: datetime) -> CurrentFacts:
        from ..services.orchestration.wealth_portfolio_evidence import product_content
        value = await self._read_groups(user_id, [('assessment', 'assessments/current'), ('products', 'products')])
        raw = value['assessment']
        business = await self.transport.get('/api/v1/wealth/assets') if value['products'] else None
        contexts = await self.transport.get('/api/v1/wealth/contexts') if value['products'] else None
        selected = next((r for r in contexts.get('items',[]) if r.get('context_id')==contexts.get('selected')),None) if isinstance(contexts,dict) else None
        assessment = None
        if raw is not None:
            # ID+完整响应派生版本；不将profile_version冒充最新画像验证。
            assessment = FormalAssessment(user_id=user_id, assessment_id=raw.get('assessment_id'),
                version='adapter:'+digest(raw), source_id='protected-local-bank', source_kind='simulated',
                capacity_level=raw.get('capacity_level'), confirmed=raw.get('confirmed') is True,
                purchase_allowed=raw.get('purchase_allowed') is True, expires_at=raw.get('expires_at'), read_at=at)
        offers = {}
        for row in value['products']:
            code = row['product_id']
            if code in offers:
                raise CandidateError('BANK_PRODUCT_ID_AMBIGUOUS', 409)
            rules, disclosure = row.get('rules', {}), row.get('disclosure', {})
            native = native_product_evidence(row)
            evidence = None
            if (native['rules_complete'] and isinstance(business,dict) and business.get('business_at')
                and selected and business.get('context_id')==contexts.get('selected')
                and row.get('dataset_key')==(selected.get('dataset_key') if contexts['selected']!='legacy-v1' else 'legacy-v1')
                and type(row.get('purchase_open')) is bool and type(row.get('data_stale')) is bool
                and row.get('version') is not None and disclosure.get('version') is not None):
                evidence = CurrentSimulationEvidence(provenance='protected-local-bank-current-products',
                    product_id=code,product_version=row['version'],disclosure_version=disclosure['version'],
                    observed_at=at,business_at=business['business_at'],calendar_version=rules['calendar_version'],
                    cutoff=rules['cutoff'],open_interval_days=rules.get('open_interval_days',1),
                    purchase_end_date=rules.get('purchase_end_date'),nav_date=row.get('nav_date'),
                    available_at=row.get('available_at'),nav=row.get('nav'),status=row['status'],
                    purchase_paused=rules['purchase_paused'],purchase_open=row['purchase_open'],data_stale=row['data_stale'],
                    product_content_sha256=digest(product_content(row)))
            # 缺销售有效区间、渠道和明确许可时保留unknown，绝不默认可买。
            offers[code] = BankPurchaseFact(product_id=code, share_class=row.get('share_class', rules.get('share_class')),
                product_version=row.get('version'), disclosure_version=disclosure.get('version'),
                rules_version='adapter:'+digest(rules) if rules else None,
                source_id='protected-local-bank', source_kind='simulated', channel='local-competition-bank',
                valid_from=rules.get('valid_from'), valid_until=rules.get('valid_until'),
                purchase_allowed=native['purchase_allowed'], rules_complete=native['rules_complete'],
                minimum_fen=rules.get('min_amount_fen'), maximum_fen=rules.get('max_amount_fen'),
                limit_kind='bounded' if rules.get('max_amount_fen') is not None else 'unknown',
                risk_level=row.get('product_risk_level'), risk_current_confirmed=native['risk_current_confirmed'],
                risk_evidence_id=native['risk_evidence_id'], holding_allowed=rules.get('holding_allowed'),
                redemption_allowed=rules.get('redemption_allowed'),current_simulation=evidence)
        return CurrentFacts(user_id=user_id, source_id='protected-local-bank', mode='simulated', channel='local-competition-bank',
            read_at=at, data_version=self.data_version, daily_version=self.daily_version,
            offers_version='adapter:'+digest(value['products']), assessment=assessment,
            latest_assessment_id=assessment.assessment_id if assessment else None,
            latest_assessment_version=assessment.version if assessment else None,
            assessment_current_proven=bool(raw and raw.get('current_profile_verified') is True), offers=offers)


class BankPortfolioAccess:
    """使用银行HTTP控制事实；既有列表缺写锁/会话证明时不颁发权限。"""
    def __init__(self, source: BankPortfolioFacts) -> None:
        self.source = source

    async def resolve(self, user_id: str, context_id: str, *, write: bool, portfolio_id: str | None = None) -> dict:
        groups = [('contexts', 'contexts'), ('assets', 'assets')]
        if write or portfolio_id: groups.append(('tasks','tasks'))
        value = await self.source._read_groups(user_id, groups)
        contexts = value['contexts']
        if contexts.get('selected') != context_id:
            raise CandidateError('PORTFOLIO_CONTEXT_FORBIDDEN', 403)
        try:
            _, ids, _, _, selected = self.source._run_accounts(value['assets'], contexts)
        except SnapshotError as error:
            raise CandidateError(error.code,error.status_code) from error
        switch = contexts.get('version')
        if type(switch) is not int or switch < 1:
            raise CandidateError('PORTFOLIO_SWITCH_VERSION_UNPROVEN',503)
        if selected.get('session_closed') is True:
            raise CandidateError('PORTFOLIO_SESSION_CLOSED',409)
        grant = {'user_id':user_id,'context_id':context_id,'context_epoch':selected.get('context_epoch'),
            'selection_version':switch,'asset_scope_id':'bank-run:'+digest({'user_id':user_id,
                'run_id':selected['run_id'],'account_ids':ids}),'account_ids':ids,'can_read':True}
        # 与既有require_plan_edit/ExistingPortfolioAccess相同的可见控制拒绝；
        # 未暴露的停止/会话字段仍不能据列表缺省推断可写。
        for task in value.get('tasks',{}).get('items',[]):
            if not isinstance(task, dict) or task.get('context_id') != context_id:
                raise CandidateError('BANK_TASK_SCOPE_UNPROVEN', 503)
            if task.get('status') not in {'COMPLETED', 'PARTIAL_COMPLETED', 'FAILED', 'CANCELLED'}:
                if write and (task.get('status') == 'PAUSED' or task.get('control_mode') != 'AGENT'):
                    raise CandidateError('TASK_WRITE_BLOCKED', 409)
            if write and portfolio_id and task.get('plan_id') == portfolio_id and task.get('control_mode') != 'AGENT':
                raise CandidateError('TASK_WRITE_BLOCKED', 409)
            linked = bool(portfolio_id and task.get('plan_id') == portfolio_id)
            active = task.get('status') not in {'COMPLETED','PARTIAL_COMPLETED','FAILED','CANCELLED'}
            if linked or write and active:
                if task.get('session_closed') is True or write and task.get('stop_new_submissions') is True:
                    raise CandidateError('TASK_WRITE_BLOCKED' if write else 'PORTFOLIO_SESSION_CLOSED',409)
                needed = ['session_closed'] + (['stop_new_submissions'] if write else [])
                if any(type(task.get(k)) is not bool for k in needed):
                    raise CandidateError('PORTFOLIO_WRITE_CONTROL_UNPROVEN' if write else 'PORTFOLIO_LINKED_SESSION_UNPROVEN',
                        503,{'missing_fields':needed,'task_id':task.get('task_id')})
                if type(task.get('version')) is not int or task['version'] < 1:
                    raise CandidateError('PORTFOLIO_LINKED_VERSION_UNPROVEN',503)
                if linked:
                    if 'related_task_version' in grant:
                        raise CandidateError('PORTFOLIO_LINKED_TASK_AMBIGUOUS',503)
                    grant['related_task_version'] = (task.get('task_id'),task['version'],task.get('session_id'))
        if write:
            # 完整任务查询无适用锁，或所有适用锁均有明确否定证据；不由AGENT推断隐藏字段。
            grant['can_edit'] = True
        return grant
