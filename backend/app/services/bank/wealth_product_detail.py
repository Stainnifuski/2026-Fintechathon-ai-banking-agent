"""Read-only scoped detail: filter publication before sending data to the frontend."""
from sqlalchemy import select
from ...models.wealth import WealthProductVersionORM,WealthNavORM
from ...models.wealth_research import WealthResearchProductORM
from ...domain.wealth_chart import chart
from ...domain.time import utc_iso
from ...domain.wealth_paper_policy import PRICING_MODES
from datetime import datetime
from . import wealth_service as bank,wealth_research as research
from .transfer_service import BankError


async def detail(db,user_id,product_id,period='1y',product_version=None):
    context=await research.selected(db,user_id)
    now=await bank.business_now(db,context.account_id if context else None)
    if context:
        from ...domain.wealth_bank_portfolio_bundle import authorized_dataset_keys
        row=await db.scalar(select(WealthResearchProductORM).where(WealthResearchProductORM.dataset_key==context.dataset_key,
            WealthResearchProductORM.product_id==product_id))
        if row is None:
            row=await db.scalar(select(WealthResearchProductORM).where(WealthResearchProductORM.dataset_key.in_(authorized_dataset_keys(context)),WealthResearchProductORM.product_id==product_id))
        if row is None:raise BankError('产品不在当前研究范围','PRODUCT_NOT_FOUND',404)
        metadata=row.data;history=row.history
        if product_version is not None and metadata.get('product_version') != product_version:
            raise BankError('当前数据集未提供该产品版本资料','RULE_VERSION_CHANGED',409)
        rules=metadata.get('rules',{});simulated=metadata.get('source')!='akshare' and not rules.get('execution_policy')
        calendar=rules.get('calendar_version','cn-trading-2021-2027-v1')
        version=metadata.get('product_version');data_version=context.dataset_key
    else:
        products=await bank.products(db,user_id);metadata=next((p for p in products if p['product_id']==product_id),None)
        if metadata is None:raise BankError('产品不存在','PRODUCT_NOT_FOUND',404)
        version=metadata['version'] if product_version is None else product_version
        if version != metadata['version']:
            bound=await db.get(WealthProductVersionORM,f'{product_id}:{version}')
            if bound is None or bound.rules.get('dataset_id'):
                raise BankError('产品版本不在当前范围','PRODUCT_NOT_FOUND',404)
            metadata={**metadata,'name':bound.name,'category':bound.category,'product_risk_level':bound.risk_level,'rules':bound.rules}
        rules=metadata['rules'];calendar=rules['calendar_version'];simulated=True;data_version='legacy-v1'
        rows=(await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id==f'{product_id}:{version}').order_by(WealthNavORM.nav_date))).all()
        history=[{'date':p.nav_date.isoformat(),'nav':p.price,'available_at':utc_iso(bank.nav_available_at(p))} for p in rows]
    history_source='existing_saved_history';history_error=None
    if rules.get('execution_policy') and len(history)<2 and product_id.isdigit() and len(product_id)==6:
        # Detail-only, bounded read of this exact verified code. Never used by
        # eligibility/Plan/price and never persisted as a new permanent cache.
        from .wealth_provider import AKShareProvider,normalize_history
        from datetime import timezone
        import asyncio
        try:
            raw=await asyncio.to_thread(AKShareProvider(timeout=8,retries=1).fetch,'fund_open_fund_info_em',symbol=product_id,indicator='单位净值走势')
            if raw.get('ok'):
                observed=datetime.now(timezone.utc)
                acquired=normalize_history(raw['records'],raw['finished_at'])
                # A controlled transaction clock does not backdate this new
                # observation. Cut NAV dates to its business day nonetheless.
                history=[p for p in acquired if p['date']<=now.astimezone(bank.TZ).date().isoformat()]
                history_source='exact_code_detail_only_akshare_unit_nav';chart_now=observed
            else:history_error=raw.get('error_type','HISTORY_UNAVAILABLE')
        except Exception:history_error='HISTORY_UNAVAILABLE'
    chart_now=locals().get('chart_now',now)
    charts={key:chart(history,as_of=chart_now,period=key,calendar_version=calendar,data_version=data_version,simulated=simulated) for key in ('1m','3m','1y')}
    for key,value in list(charts.items()):
        if len(value['points'])<2:charts[key]=None
    result=charts[period]
    published=[p for p in history if datetime.fromisoformat(p['available_at'])<=now and p['date']<=now.astimezone(bank.TZ).date().isoformat()]
    latest=max(published,key=lambda p:p['date']) if published else None
    paper=rules.get('execution_policy') if rules.get('pricing_mode') in PRICING_MODES else None
    reference=await bank.latest_nav(db,f'{product_id}:{version}',now) if paper else None
    return {'product_id':product_id,'name':metadata['name'],'product_version':version,
        'identity':metadata.get('identity','真实公开基金资料＋比赛模拟交易') if paper else metadata.get('identity','虚构产品与模拟行情') if simulated else '真实基金数据／模拟资金交易环境',
        'underlying_source_kind':'real' if paper or not simulated else 'simulated','execution_source_kind':'simulated',
        'risk_source_kind':'project_policy' if paper else None,'project_policy_version':paper.get('version') if paper else None,
        'public_risk_rating':metadata.get('public_risk_rating') if paper else None,
        'public_nav_date_basis':paper['quote'].get('date_basis') if paper else None,
        'category':metadata.get('category'),'risk_level':metadata.get('risk_level',metadata.get('product_risk_level')),
        'rules':{k:rules.get(k) for k in ('min_amount_fen','max_amount_fen','holding_days','cash_business_days','confirm_business_days','cutoff','purchase_fee_rate','sales_service_fee_rate','redeem_fee_bands','quick_redemption')},'source':metadata.get('official_reference',metadata.get('source_url')),
        'as_of':utc_iso(now),'latest_nav':{'date':latest['date'],'nav':latest['nav']} if latest else None,
        'chart':result,'charts':charts,'history_source':history_source,'history_error':history_error,
        'execution_price':{'value':reference.price,'date':reference.nav_date.isoformat(),'source':'public_observed_nav_preview_only'} if reference else None,
        'simulation':simulated,'data_version':data_version}
