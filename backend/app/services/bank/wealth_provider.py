"""隔离进程限制上游请求；统一真实数据结构，不在交易请求联网。"""
import csv
import io
import json
import multiprocessing as mp
import time
from datetime import datetime, timezone
from decimal import Decimal

AKSHARE_VERSION="1.19.1"
SOURCE_URL="https://fund.eastmoney.com/"
FUNDS=("000001","000003","000004","000008","000011","000021","000041","000051","000061","000071")
MACRO_FIELDS={
 "macro_china_lpr":{"lpr_1y":("LPR1Y",),"lpr_5y":("LPR5Y",)},
 "macro_china_cpi":{"cpi_yoy":("全国-同比增长","全国-同比")},
 "macro_china_ppi":{"ppi_yoy":("当月同比增长","同比增长")},
 "macro_china_pmi":{"pmi_manufacturing":("制造业-指数",),"pmi_nonmanufacturing":("非制造业-指数",)},
 "macro_china_supply_of_money":{"m2_yoy":("货币和准货币（广义货币M2）同比增长","货币和准货币(M2)-同比增长","货币和准货币(M2)-同比"),"m1_yoy":("货币(狭义货币M1)同比增长","货币(M1)-同比增长","货币(M1)-同比")}}


def _worker(queue,method,kwargs):
    """上游不保证统一timeout参数；硬进程截止保障整个接口有界。"""
    try:
        import akshare as ak
        if ak.__version__!=AKSHARE_VERSION: raise ValueError("AKSHARE_VERSION_MISMATCH")
        frame=getattr(ak,method)(**kwargs)
        queue.put({"ok":True,"columns":list(frame.columns),"records":json.loads(frame.to_json(orient="records",force_ascii=False,date_format="iso"))})
    except Exception as exc:
        queue.put({"ok":False,"error_type":type(exc).__name__})


class AKShareProvider:
    version=AKSHARE_VERSION
    def __init__(self,timeout: int=35,retries: int=3):
        self.timeout=min(max(timeout,1),90); self.retries=min(max(retries,1),3)
        self.failures=0; self.open_until=0

    def fetch(self,method: str,**kwargs) -> dict:
        allowed={"fund_name_em","fund_purchase_em","fund_open_fund_info_em",*MACRO_FIELDS}
        if method not in allowed: raise ValueError("PROVIDER_METHOD_DENIED")
        if time.monotonic()<self.open_until: return {"ok":False,"error_type":"CIRCUIT_OPEN","method":method}
        started=datetime.now(timezone.utc).isoformat(); response={}
        for attempt in range(self.retries):
            ctx=mp.get_context("spawn"); queue=ctx.Queue(); process=ctx.Process(target=_worker,args=(queue,method,kwargs)); process.start()
            # Read queue while producer is alive, avoiding pipe-buffer join deadlocks.
            try: response=queue.get(timeout=self.timeout)
            except Exception: response={"ok":False,"error_type":"PROVIDER_TIMEOUT"}
            finally:
                if process.is_alive(): process.terminate()
                process.join(timeout=3); queue.close()
            if response["ok"]: self.failures=0; break
            if attempt+1<self.retries: time.sleep(2**attempt)
        if not response.get("ok"):
            self.failures+=1
            if self.failures>=3: self.open_until=time.monotonic()+60
        return {**response,"method":method,"provider_version":self.version,"source_url":"https://data.eastmoney.com/cjsj/" if method.startswith("macro_") else SOURCE_URL,
                "started_at":started,"finished_at":datetime.now(timezone.utc).isoformat(),"attempts":attempt+1}


def normalize_history(records: list[dict], ingested_at: str) -> list[dict]:
    rows={}
    for r in records:
        if not {"净值日期","单位净值"}<=r.keys(): raise ValueError("HISTORY_SCHEMA_DRIFT")
        date=str(r["净值日期"])[:10]
        datetime.fromisoformat(date)
        if date>ingested_at[:10]: raise ValueError("FUTURE_OBSERVATION")
        if r["单位净值"] is None: continue
        nav=Decimal(str(r["单位净值"]))
        if not nav.is_finite() or nav<=0: raise ValueError("NAV_NONPOSITIVE")
        if date in rows: raise ValueError("DUPLICATE_NAV_DATE")
        rows[date]={"date":date,"nav":format(nav.quantize(Decimal("0.00000001")),"f"),"published_at":None,
                    "available_at":ingested_at,"dividend_per_unit":None}
    return [rows[d] for d in sorted(rows)]


def parse_import(raw: bytes,format: str) -> dict:
    """接收内容而非路径/URL；管理员CLI读取受控文件，模型不触达。"""
    if len(raw)>25_000_000: raise ValueError("INPUT_TOO_LARGE")
    if format=="json": return json.loads(raw.decode("utf-8-sig"))
    if format!="csv": raise ValueError("FORMAT_UNSUPPORTED")
    rows=list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows: raise ValueError("EMPTY_IMPORT")
    identity={(r["dataset_id"],r["dataset_version"],r["mode"]) for r in rows}
    if len(identity)!=1: raise ValueError("MIXED_DATASETS")
    did,version,mode=identity.pop(); groups={}
    for r in rows:
        p=groups.setdefault(r["product_id"],{"product_id":r["product_id"],"name":r["name"],"source":"admin_import",
            "source_product_code":r.get("source_product_code"),"rules_complete":False,"history":[]})
        p["history"].append({"date":r["date"],"nav":r["nav"],"available_at":r["available_at"],"published_at":r.get("published_at") or None})
    for p in groups.values(): p["history"].sort(key=lambda r:r["date"])
    return {"schema_version":1,"dataset_id":did,"dataset_version":version,"mode":mode,"as_of":max(r["date"] for r in rows),"products":list(groups.values())}


def normalize_macro(records: list[dict], date_field: str, field: str, indicator: str, downloaded: str) -> list[dict]:
    import re
    rows={}
    for row_number, r in enumerate(records,1):
        if r[field] is None: continue
        parts=re.findall(r'\d+',str(r[date_field]))
        if len(parts)<2: raise ValueError(f'MACRO_DATE_ROW_{row_number}')
        period=f'{int(parts[0]):04}-{int(parts[1]):02}'
        datetime.fromisoformat(period+'-01')
        if period>downloaded[:7]: raise ValueError(f'FUTURE_MACRO_ROW_{row_number}')
        number=Decimal(str(r[field]))
        if not number.is_finite(): raise ValueError(f'MACRO_VALUE_ROW_{row_number}')
        # LPR can have several announcements within a month: retain exact day.
        if len(parts)>=3: period += f'-{int(parts[2]):02}'
        rows[period]={'period':period,'value':str(number),'unit':'index' if 'pmi' in indicator else 'percent',
            'published_at':None,'available_at':downloaded,'revision':downloaded,'source':'akshare',
            'source_url':'https://data.eastmoney.com/cjsj/','definition_break':'M1-definition-2025' if indicator=='m1_yoy' else None}
    return [rows[k] for k in sorted(rows)]


def probe(provider: AKShareProvider,codes=FUNDS) -> dict:
    """有限10份额探测；任何接口失败保留真实失败，不合成补齐。"""
    name=provider.fetch("fund_name_em"); status=provider.fetch("fund_purchase_em")
    directory={str(r.get("基金代码","")).zfill(6):r for r in name.get("records",[])}
    purchase={str(r.get("基金代码","")).zfill(6):r for r in status.get("records",[])}
    products=[]; report=[]
    for code in codes:
        raw=provider.fetch("fund_open_fund_info_em",symbol=code,indicator="单位净值走势")
        entry={k:v for k,v in raw.items() if k!="records"}; entry.update(code=code,row_count=len(raw.get("records",[])))
        try:
            if not raw["ok"] or code not in directory: raise ValueError("SOURCE_UNAVAILABLE")
            history=normalize_history(raw["records"],raw["finished_at"])
            identity=directory[code]; subtype=identity.get("基金类型","未知")
            category="bond" if "债" in subtype else "index" if "指数" in subtype else "cash" if "货币" in subtype else "mixed" if "混合" in subtype else "equity"
            # Verified pair from explicitly cited upstream fund catalogue, not fuzzy auto linkage.
            family="REAL-000003-000004" if code in {"000003","000004"} and identity.get("基金简称") in {"中海可转债债券A","中海可转债债券C"} else None
            products.append({"product_id":"R"+code,"source_product_code":code,"name":identity["基金简称"],"source":"akshare",
                "share_class":"A" if code=="000003" and family else "C" if code=="000004" and family else None,
                "fund_family_id":family,"family_evidence_url":"https://akshare.akfamily.xyz/data/fund/fund_public.html" if family else None,
                "category":category,"subtype":subtype,"rules_complete":False,"missing_rules":["reviewed_risk","full_fees","holding","settlement"],
                "status_snapshot":purchase.get(code),"status_available_at":status["finished_at"],"history":history,
                "quality_flags":["publication_time_unknown","unit_nav_not_total_return","research_only_rules_missing"]})
            entry.update(ok=True,start=history[0]["date"],end=history[-1]["date"])
        except Exception as exc: entry.update(ok=False,error_type=type(exc).__name__)
        report.append(entry)
    macros=[]
    for method,fields in MACRO_FIELDS.items():
        raw=provider.fetch(method)
        report.append({**{k:v for k,v in raw.items() if k!="records"},"row_count":len(raw.get("records",[]))})
        if not raw["ok"]: continue
        for indicator,aliases in fields.items():
            field=next((f for f in aliases if f in raw["columns"]),None)
            date_field=next((f for f in ("TRADE_DATE","日期","月份","统计时间","时间") if f in raw["columns"]),None)
            if not field or not date_field:
                report.append({"method":method,"indicator":indicator,"ok":False,"error_type":"MACRO_SCHEMA_DRIFT"}); continue
            try: observations=normalize_macro(raw['records'],date_field,field,indicator,raw['finished_at'])
            except ValueError as exc:
                report.append({'method':method,'indicator':indicator,'ok':False,'error_type':str(exc)}); continue
            macros.append({"indicator":indicator,"observations":observations})
    now=datetime.now(timezone.utc).isoformat()
    dataset={"schema_version":1,"dataset_id":"real-funds","dataset_version":now.replace(":","").replace(".",""),"mode":"snapshot",
        "source":"akshare","provider_version":provider.version,"as_of":max((p["history"][-1]["date"] for p in products),default=now[:10]),"products":products}
    return {"dataset":dataset,"macros":macros,"report":[{**{k:v for k,v in r.items() if k!="records"},"row_count":len(r.get("records",[]))} for r in (name,status)]+report}
