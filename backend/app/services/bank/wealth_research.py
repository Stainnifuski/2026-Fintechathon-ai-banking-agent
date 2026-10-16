"""4号数据集导入/资金上下文；不可变版本，不覆盖既有资金事实。"""
import json
from ...domain.wealth_paper_policy import PRICING_MODES
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ...domain.wealth_synthetic import fingerprint, DATASET_ID
from ...domain.wealth_fixed_data import synthetic_payload,DATASET_VERSION
from ...models.account import AccountORM
from ...models.wealth import WealthProductORM, WealthProductVersionORM, WealthNavORM, WealthOrderORM, WealthAuthorizationORM, WealthConversationORM
from ...models.wealth_research import WealthDatasetORM, WealthResearchProductORM, WealthContextORM, WealthContextSelectionORM
from .transfer_service import BankError
from ...domain.time import BEIJING

MAX_IMPORT_BYTES=150_000_000 # bounded offline full-core dataset, not an HTTP request limit


def validate(payload: dict) -> list[dict]:
    errors=[]
    if not isinstance(payload,dict) or payload.get("schema_version")!=1:
        return [{"row":0,"code":"SCHEMA_VERSION"}]
    if len(json.dumps(payload).encode())>MAX_IMPORT_BYTES:
        return [{"row":0,"code":"INPUT_TOO_LARGE"}]
    if payload.get("mode") not in {"synthetic","snapshot","live_sync"} or not payload.get("dataset_id") or not payload.get("dataset_version"):
        errors.append({"row":0,"code":"DATASET_IDENTITY"})
    seen=set()
    for n,p in enumerate(payload.get("products",[]),1):
        try:
            pid=p["product_id"]
            if not isinstance(pid,str) or not 1<=len(pid)<=32 or pid in seen: raise ValueError("IDENTITY_DUPLICATE")
            seen.add(pid)
            if not p.get("name") or p.get("source") not in {"synthetic","akshare","admin_import"}: raise ValueError("SOURCE_MISSING")
            if p.get("source")=="akshare" and p.get("category") not in {"bond","mixed","equity","index"}: raise ValueError("UNSUPPORTED_REAL_FUND_CATEGORY")
            if p.get('rules_complete'):
                rules=p['rules']
                for field in ('currency','min_amount_fen','max_amount_fen','purchase_fee_rate','redeem_fee_bands','holding_days','confirm_business_days','cash_business_days','calendar_version','rules_source','cutoff','cancel_before_pricing','purchase_paused','redeem_paused'): rules[field]
                if rules['currency']!='CNY' or type(p['risk_level']) is not int or not 1<=p['risk_level']<=5: raise ValueError('INVALID_SUITABILITY_RULES')
                if p['category'] not in {'cash','bond','mixed','equity','index'}: raise ValueError('INVALID_CATEGORY')
                if any(type(rules[k]) is not int or rules[k]<0 for k in ('min_amount_fen','max_amount_fen','holding_days','confirm_business_days','cash_business_days')) or rules['min_amount_fen']>rules['max_amount_fen']: raise ValueError('INVALID_AMOUNT_RULES')
                if not 0<=Decimal(rules['purchase_fee_rate'])<1: raise ValueError('INVALID_FEE_RULES')
                if not rules['redeem_fee_bands'] or any(not 0<=Decimal(b['rate'])<1 or b['days_lt']<=0 for b in rules['redeem_fee_bands']): raise ValueError('INVALID_FEE_RULES')
                for field in ('kind','product_version','disclosure_version'): p[field]
                if rules.get('pricing_mode') in PRICING_MODES:
                    from ...domain.wealth_paper_policy import validate_policy,registered_policy_reasons
                    failures=validate_policy(rules.get('execution_policy'),pid,p['category'],p['risk_level'])
                    failures+=registered_policy_reasons(rules.get('execution_policy'),pid)
                    if failures:raise ValueError(','.join(failures))
            last=None
            for point in p["history"]:
                d=date.fromisoformat(point["date"])
                if last is not None and d<=last: raise ValueError("DATES_UNSORTED_OR_DUPLICATE")
                last=d; nav=Decimal(str(point["nav"]))
                if not nav.is_finite() or nav<=0: raise ValueError("NAV_NONPOSITIVE")
                available=datetime.fromisoformat(point["available_at"])
                if available.tzinfo is None: raise ValueError("AVAILABILITY_TIME_REQUIRED")
            if not p["history"]: raise ValueError("EMPTY_HISTORY")
        except (KeyError,TypeError,ValueError,ArithmeticError) as exc:
            errors.append({"row":n,"code":str(exc)[:80]})
    if not payload.get("products"): errors.append({"row":0,"code":"EMPTY_PRODUCTS"})
    return errors


async def import_dataset(db: AsyncSession, payload: dict, dry_run: bool=False) -> dict:
    """内容指纹决定不可变批次；同标识异内容必须显式新版本。"""
    errors=validate(payload)
    if errors: return {"ok":False,"errors":errors,"written":0}
    key=f'{payload["dataset_id"]}:{payload["dataset_version"]}'
    if len(key)>96: raise BankError("数据标识过长","INVALID_DATASET",422)
    digest=fingerprint({k:v for k,v in payload.items() if k!="fingerprint"})
    existing=await db.get(WealthDatasetORM,key)
    if existing:
        if existing.manifest["fingerprint"]!=digest: raise BankError("已有版本不可覆盖；请创建修订版本","IDEMPOTENCY_CONFLICT",409)
        return {"ok":True,"dataset_key":key,"written":0,"duplicate":True}
    if dry_run: return {"ok":True,"dataset_key":key,"written":0,"validated":len(payload["products"])}
    now=datetime.now(timezone.utc)
    manifest={k:v for k,v in payload.items() if k not in {"products","fingerprint"}}
    manifest.update(fingerprint=digest,product_count=len(payload["products"]),ingested_at=now.isoformat())
    db.add(WealthDatasetORM(dataset_key=key,dataset_id=payload["dataset_id"],version=payload["dataset_version"],mode=payload["mode"],manifest=manifest,ingested_at=now))
    await db.flush()
    # One immutable catalogue read, not two round trips and two flushes per code.
    existing_versions={p.version_id:p for p in (await db.scalars(select(WealthProductVersionORM))).all()}
    existing_products={p.product_id:p for p in (await db.scalars(select(WealthProductORM))).all()}
    for p in payload['products']:
        if not p.get('rules_complete') or not p.get('rules'):continue
        pid=p['product_id'];version=int(p.get('product_version',1))
        old=existing_versions.get(f'{pid}:{version}')
        if old and old.rules!=p['rules']:raise BankError('银行规则版本冲突','RULE_VERSION_CHANGED',409)
        current=existing_products.get(pid)
        if current is None:
            current=WealthProductORM(product_id=pid,current_version=version,status='on_sale')
            db.add(current);existing_products[pid]=current
        elif version>current.current_version:current.current_version=version
    await db.flush()
    nav_rows=[]
    for p in payload["products"]:
        history=p["history"]; metadata={k:v for k,v in p.items() if k!="history"}
        db.add(WealthResearchProductORM(record_id=f'{key}:{p["product_id"]}',dataset_key=key,product_id=p["product_id"],data=metadata,history=history))
        # Only reviewed, complete simulation overlays enter the bank catalogue.
        if not p.get("rules_complete") or not p.get("rules"): continue
        pid=p["product_id"]; version=int(p.get("product_version",1)); vid=f"{pid}:{version}"
        old=existing_versions.get(vid)
        if old:
            if old.rules!=p["rules"]: raise BankError("银行规则版本冲突","RULE_VERSION_CHANGED",409)
            continue
        disclosure={"version":version,"nature":"明确模拟规则覆盖的净值型产品","manager":p.get("manager","资料未提供"),
            "seller":"比赛模拟银行","risk":"本金可能亏损；风险等级为项目模拟分级","fees":"年度费用已含净值；申赎费用单独计算，不重复扣费",
            "liquidity":"按版本化模拟营业日确认到账","holding_period_days":p["rules"]["holding_days"],
            "pricing":"只用指定计价日可见净值；缺失等待","settlement":"按模拟规则确认和到账，不是发行人承诺",
            "cancellation":"计价截止前且未计价可撤销","limitations":"不连接真实交易；模拟资金与真实公开资料分别标源",
            "dataset_id":payload["dataset_id"],"dataset_version":payload["dataset_version"],"data_source":p["source"],"rules_source":p["rules"]["rules_source"]}
        if p.get('official_reference'):
            disclosure.update(identity=p['identity'], official_reference=p['official_reference'],
                source_rules=p['source_rules'], rules_adaptations=p['rules_adaptations'])
        if p['rules'].get('pricing_mode') in PRICING_MODES:
            policy=p['rules']['execution_policy']
            disclosure.update(identity='真实公开基金资料＋比赛模拟交易',
                underlying_source_kind='real',execution_source_kind='simulated',source_product_code=pid,
                project_policy_version=policy['version'],issuer_risk_rating=p.get('public_risk_rating'),
                risk='项目风险分级独立于发行人评级；可靠公开更高等级或分类冲突停止申购；不承诺保本',
                # Preserve this immutable source version on fresh imports too.
                # Current pricing is a separately versioned runtime overlay.
                pricing='使用本版本冻结的最后公开NAV作为模拟成交基准，公开NAV日期与模拟计价日分别记录',
                fees='项目模拟申购和赎回费用为0，无额外销售服务费；公开费用原值另存，未知不补零',
                limitations='真实资料不代表真实代销或真实成交；价格冻结，不填未来行情',
                public_sources=p.get('public_sources',[]),public_fields=p.get('public_fields',{}))
        if p['rules'].get('quick_redemption'):
            disclosure.update(quick_redemption=p['rules']['quick_redemption'],
                cancellation='普通申购/赎回仅计价日15:30前全撤；模拟快赎成功受理后不可撤',
                settlement='普通按T+1模拟日历；快赎同事务减少份额并即时增加可用现金，无垫资/后续清算',
                limitations='虚构现金管理理财和净值，不是真实快赎服务；每个产品本人跨账户自然日成功支付累计1万元')
        db.add(WealthProductVersionORM(version_id=vid,product_id=pid,version=version,name=p["name"],kind=p["kind"],category=p["category"],risk_level=p["risk_level"],rules=p["rules"],disclosure=disclosure,created_at=now))
        from ...domain.time import as_utc
        nav_rows.extend([WealthNavORM(nav_id=f'{vid}:{point["date"]}',version_id=vid,nav_date=date.fromisoformat(point["date"]),price=point["nav"],
            available_at=as_utc(datetime.fromisoformat(point['available_at']))) for point in history])
    await db.flush()
    db.add_all(nav_rows)
    await db.flush()
    return {"ok":True,"dataset_key":key,"written":len(payload["products"]),"fingerprint":digest}


async def selected(db: AsyncSession,user_id: str) -> WealthContextORM | None:
    selection=await db.get(WealthContextSelectionORM,user_id)
    if not selection or selection.context_id=="legacy-v1": return None
    context=await db.get(WealthContextORM,selection.context_id)
    if context is None or context.user_id!=user_id: raise BankError("上下文不可用","DATASET_MISMATCH",409)
    if context.data.get('run_state','ACTIVE')!='ACTIVE': raise BankError('测试轮次已归档','ARCHIVED_RUN',409)
    return context


async def select_context(db: AsyncSession,user_id: str,context_id: str,version: int) -> dict:
    # Serialize changes with preview creation through the same user/profile lock.
    from ...integrations.wealth_authorization import lock_profile
    await lock_profile(db,user_id)
    row=await db.get(WealthContextSelectionORM,user_id,with_for_update=True)
    if version!=(row.version if row else 1): raise BankError("上下文已改变，请刷新","STALE_PLAN",409)
    if context_id!="legacy-v1":
        context=await db.get(WealthContextORM,context_id)
        if context is None or context.user_id!=user_id: raise BankError("上下文不存在","NOT_FOUND",404)
        if context.data.get('run_state','ACTIVE')!='ACTIVE': raise BankError('不能选择归档轮次继续写入','ARCHIVED_RUN',409)
    if row is None: row=WealthContextSelectionORM(user_id=user_id,context_id=context_id,version=2); db.add(row)
    else: row.context_id=context_id; row.version+=1
    await db.execute(update(WealthAuthorizationORM).where(WealthAuthorizationORM.user_id==user_id,WealthAuthorizationORM.status.in_(["AWAITING_CONFIRMATION","AWAITING_MFA","VALID"])).values(status="EXPIRED"))
    conversations=(await db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id==user_id))).all()
    for conv in conversations:
        conv.version+=1
        # 会话始终属于创建时的资金范围，不能把旧轮次历史改绑到新轮次。
        conv.data={**conv.data,'scope_archived':conv.data.get('context_id','legacy-v1')!=context_id,
            'scope_switch_at':datetime.now(timezone.utc).isoformat()}
    from ...models.wealth import WealthPlanORM
    await db.execute(update(WealthPlanORM).where(WealthPlanORM.user_id==user_id,WealthPlanORM.status.in_(['DRAFT','AWAITING_CONFIRMATION'])).values(status='EXPIRED'))
    await db.commit()
    return {"context_id":context_id,"version":row.version}


async def context_list(db: AsyncSession,user_id: str) -> dict:
    current=await db.get(WealthContextSelectionORM,user_id)
    rows=(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==user_id))).all()
    return {"selected":current.context_id if current else "legacy-v1","version":current.version if current else 1,
        "items":[{"context_id":"legacy-v1","label":"原银行账本（兼容8产品）","mode":"legacy"}]+[
            {"context_id":r.context_id,"account_id":r.account_id,"dataset_key":r.dataset_key,**r.data} for r in rows]}


async def initialize(db: AsyncSession,user_id: str,scenario: str="normal",balance_fen: int=2000000,dataset_key: str | None=None) -> dict:
    """管理员显式初始化一次；切换不创建或复制余额。"""
    from ...domain.wealth_scenarios import SCENARIOS
    if scenario not in SCENARIOS: raise ValueError('SCENARIO_UNKNOWN')
    fixture=SCENARIOS[scenario]
    if dataset_key and dataset_key!=f'{DATASET_ID}:{DATASET_VERSION}':
        dataset=await db.get(WealthDatasetORM,dataset_key)
        if dataset is None or dataset.mode not in {'snapshot','live_sync'} or scenario!='normal': raise ValueError('RESEARCH_DATASET_REQUIRED')
        cid='CTX'+fingerprint([user_id,dataset_key,'research'])[:28]
        if await db.get(WealthContextORM,cid): return {'context_id':cid,'created':False}
        aid='SA'+fingerprint([cid,'account'])[:28]
        paper=dataset.manifest.get('policy_version') is not None
        db.add(AccountORM(account_id=aid,user_id=user_id,account_type='savings',card_number='模拟研究****'+aid[-4:],balance_fen=balance_fen,daily_transfer_used_fen=0,daily_transfer_limit_fen=5000000,status='active'))
        await db.flush()
        db.add(WealthContextORM(context_id=cid,user_id=user_id,account_id=aid,dataset_key=dataset_key,version=1,data={'label':'真实基金公开资料｜比赛模拟交易｜模拟资金' if paper else '真实基金数据｜研究演示｜模拟资金','mode':dataset.mode,'scenario':'research','fixture':{},'disposable':True,'business_at':datetime.now(timezone.utc).isoformat()}))
        from .audit_service import record_bank_event
        record_bank_event(db,event_key=cid,subject_user_id=user_id,action='investment_research_sandbox_initialize',result='success',facts={'context_id':cid,'issued_simulated_cash_fen':balance_fen,'dataset_key':dataset_key},decision_chain=['explicit_admin_initialization','new_account_no_balance_copy','approved_real_public_paper_policy' if paper else 'research_only_rules_missing'])
        await db.flush(); return {'context_id':cid,'account_id':aid,'created':True}
    key=f"{DATASET_ID}:{DATASET_VERSION}"
    if await db.get(WealthDatasetORM,key) is None:
        result=await import_dataset(db,synthetic_payload())
        if not result["ok"]: raise ValueError(result)
    from .wealth_knowledge import seed
    from ...models.wealth_research import WealthMacroORM
    products=(await db.scalars(select(WealthResearchProductORM).where(WealthResearchProductORM.dataset_key==key))).all()
    await seed(db,[p.data for p in products])
    for n,indicator in enumerate(('lpr_1y','lpr_5y','cpi_yoy','ppi_yoy','pmi_manufacturing','pmi_nonmanufacturing','m1_yoy','m2_yoy')):
        rid=key+':'+indicator
        if await db.get(WealthMacroORM,rid) is None:
            db.add(WealthMacroORM(record_id=rid,dataset_key=key,indicator=indicator,observations=[{'period':f'2026-{month:02}', 'value':str(Decimal(50 if 'pmi' in indicator else 3)+Decimal(month+n)/10),'unit':'index' if 'pmi' in indicator else 'percent','published_at':f'2026-{month:02}-28T18:00:00+08:00','available_at':f'2026-{month:02}-28T18:00:00+08:00','source':'synthetic','definition_break':'M1-definition-2025' if indicator=='m1_yoy' else None} for month in range(1,10)]))
    context_id="CTX"+fingerprint([user_id,key,scenario])[:28]
    old=await db.get(WealthContextORM,context_id)
    if old: return {"context_id":context_id,"created":False}
    account_id="SA"+fingerprint([context_id,"account"])[:28]
    db.add(AccountORM(account_id=account_id,user_id=user_id,account_type="savings",card_number="模拟沙箱****"+account_id[-4:],
        balance_fen=balance_fen,daily_transfer_used_fen=0,daily_transfer_limit_fen=5000000,status="active"))
    await db.flush()
    db.add(WealthContextORM(context_id=context_id,user_id=user_id,account_id=account_id,dataset_key=key,version=1,
        data={"label":f"v1.2虚构场景：{fixture['label']}","mode":"synthetic","scenario":scenario,"fixture":fixture,"disposable":True,"business_at":"2026-09-30T19:00:00+08:00"}))
    from .audit_service import record_bank_event
    record_bank_event(db,event_key=context_id,subject_user_id=user_id,action='investment_sandbox_initialize',result='success',facts={'context_id':context_id,'account_id':account_id,'issued_simulated_cash_fen':balance_fen,'dataset_key':key,'scenario':scenario},decision_chain=['explicit_admin_initialization','new_account_no_balance_copy','synthetic_funds'])
    await db.flush()
    return {"context_id":context_id,"created":True,"account_id":account_id}


async def catalog(db: AsyncSession,dataset_key: str | None=None,as_of: datetime | None=None) -> list[dict]:
    key=dataset_key or await default_dataset_key(db)
    dataset=await db.get(WealthDatasetORM,key)
    if dataset is None: return []
    at=as_of or (datetime.fromisoformat(dataset.manifest["as_of"]+"T23:59:59+08:00") if dataset.mode=="synthetic" else datetime.now(timezone.utc))
    rows=(await db.scalars(select(WealthResearchProductORM).where(WealthResearchProductORM.dataset_key==key).order_by(WealthResearchProductORM.product_id))).all()
    result=[]
    for row in rows:
        visible=[p for p in row.history if datetime.fromisoformat(p["available_at"])<=at and p["date"]<=at.astimezone(BEIJING).date().isoformat()]
        latest=visible[-1] if visible else None
        result.append({**row.data,"dataset_key":key,"nav":latest["nav"] if latest else None,"nav_date":latest["date"] if latest else None,
                       "available_at":latest["available_at"] if latest else None,"published_at":latest.get("published_at") if latest else None,
                       "history_count":len(visible),"history":visible,"quality_flags":row.data.get("quality_flags",[]),
                       "trade_eligible":bool(row.data.get("rules_complete")),"unavailable_reason":None if visible else "NO_VISIBLE_HISTORY"})
    return result


async def default_dataset_key(db: AsyncSession) -> str:
    latest = (await db.scalars(select(WealthDatasetORM).where(
        WealthDatasetORM.dataset_id == 'mixed-preload-v12').order_by(
        WealthDatasetORM.ingested_at.desc(), WealthDatasetORM.dataset_key.desc()))).first()
    return latest.dataset_key if latest else f"{DATASET_ID}:{DATASET_VERSION}"
