"""固定因子生成器。模拟周一至周五日历，不冒充真实中国节假日。"""
import hashlib
import json
import random
from datetime import date, timedelta
from decimal import Decimal, localcontext

SEED = 20261001
GENERATOR_VERSION = "factor-v1"
DATASET_ID = "synthetic-v12"
DATASET_VERSION = "20260930-factor-v1"
AS_OF = "2026-09-30"
CALENDAR = "synthetic-weekdays-v1"
GROUPS = (("money",6,"cash",1,"晨露"), ("bond",12,"bond",2,"青石"),
          ("broad_index",10,"index",4,"光合"), ("sector",6,"equity",5,"星野"),
          ("active",10,"mixed",3,"云帆"), ("bank_wealth",16,"bond",2,"远山"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode()).hexdigest()


def calendar(start: date, end: date) -> list[date]:
    return [start+timedelta(days=i) for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5]


def generate(seed: int = SEED, end: str = "2027-12-31") -> dict:
    """60份额/30家族，共同因子含确定下跌及债券回撤，A/C共享家族扰动。"""
    days = calendar(date(2021,10,1),date.fromisoformat(end))
    rng = random.Random(seed)
    factors=[]
    for i,day in enumerate(days):
        equity=Decimal(rng.randrange(-140,141))/10000+Decimal("0.00015")
        bond=Decimal(rng.randrange(-18,19))/10000+Decimal("0.00008")
        credit=Decimal(rng.randrange(-12,13))/10000
        liquidity=Decimal(rng.randrange(-6,7))/10000
        if date(2022,3,1)<=day<=date(2022,4,15): equity-=Decimal("0.004")
        if date(2022,11,1)<=day<=date(2022,11,18): bond-=Decimal("0.003")
        factors.append((equity,bond,credit,liquidity))
    products=[]; serial=0
    with localcontext() as ctx:
        ctx.prec=32
        for group,count,category,risk,prefix in GROUPS:
            for j in range(count):
                serial+=1; pid=f"S{serial:03}"; family=f"SF{serial//2+(serial%2):03}"
                share="A" if j%2==0 else "C"
                start=date(2026,4,1) if serial in {9,10,35,36,59,60} else days[0]
                family_rng=random.Random(f"{seed}:{family}")
                price=Decimal("1"); history=[]; recent_income=[]
                bank_category=("cash" if j<4 else "mixed" if j>=12 else "bond") if group=="bank_wealth" else category
                product_risk=1 if bank_category=="cash" else (3 if bank_category=="mixed" else risk)
                load={"cash":("0","0.04","0","0.04"),"bond":("0.04","0.78","0.2","0.08"),
                      "mixed":("0.5","0.4","0.1","0.1"),"equity":("1.2","0.02","0.03","0.15"),
                      "index":("0.95","0.02","0.02","0.05")}[bank_category]
                annual_fee=Decimal("0.003")+(Decimal("0.003") if share=="C" else 0)
                for day,factor in zip(days,factors):
                    perturb=Decimal(family_rng.randrange(-6,7))/10000
                    if day<start: continue
                    daily=sum((Decimal(a)*b for a,b in zip(load,factor)),Decimal(0))+perturb/5-annual_fee/252
                    if bank_category=="cash": daily=Decimal("0.00006")+perturb/50-annual_fee/252
                    price=(price*(1+daily)).quantize(Decimal("0.00000001"))
                    item={"date":day.isoformat(),"nav":format(price,".8f"),"published_at":day.isoformat()+"T18:00:00+08:00",
                          "available_at":day.isoformat()+"T18:00:00+08:00","dividend_per_unit":"0"}
                    if bank_category=="cash":
                        recent_income.append(daily)
                        item.update(income_per_10000=format(daily*10000,".4f"),seven_day_annualized=format(sum(recent_income[-7:])/7*365*100,".4f") if len(recent_income)>=7 else None,
                            income_basis='last_7_simulated_business_observations_simple_annualization')
                    history.append(item)
                lock=(0,30,90,180)[j%4] if group=="bank_wealth" else 0
                subtype=("short_bond","pure_bond","convertible_bond")[j%3] if group=="bond" else group
                rules={"currency":"CNY","min_amount_fen":10000,"max_amount_fen":5000000 if serial%7==0 else 200000000,
                       "purchase_fee_rate":"0" if bank_category=="cash" or share=="C" else "0.003",
                       "redeem_fee_bands":[{"days_lt":7,"rate":"0.015"},{"days_lt":30,"rate":"0.005"},{"days_lt":1000000,"rate":"0"}],
                       "holding_days":lock,"confirm_business_days":1,"cash_business_days":2 if bank_category!="cash" else 1,
                       "cutoff":"15:00","calendar_version":CALENDAR,"open_interval_days":30 if group=="bank_wealth" and j%4==3 else 1,
                       "cancel_before_pricing":True,"purchase_paused":False,"redeem_paused":False,
                       "annual_management_rate":"0.002","annual_custody_rate":"0.001","annual_service_rate":"0.003" if share=="C" else "0",
                       "annual_fees_in_nav":True,"limit_scope":"per_order_simulated","rules_source":"self_authored_simulation_v12",
                       "dataset_id":DATASET_ID,"dataset_version":DATASET_VERSION,"fund_family_id":family}
                products.append({"product_id":pid,"source_product_code":None,"share_class":share,"fund_family_id":family,
                    "name":f"模拟{prefix}{j//2+1:02}{share}","manager":"虚构青穗研究机构","inception_date":start.isoformat(),
                    "kind":"bank_wealth" if group=="bank_wealth" else "open_fund","group":group,"subtype":subtype,"category":bank_category,
                    "risk_level":product_risk,"currency":"CNY","benchmark":f"虚构{group}基准","exposure":dict(zip(("equity","bond","credit","liquidity"),load)),
                    "source":"synthetic","dataset_id":DATASET_ID,"dataset_version":DATASET_VERSION,"product_version":1,"disclosure_version":1,
                    "rules_version":"simulation-v12-1","rules":rules,"simulated":True,"rules_complete":True,"history":history,
                    "quality_flags":["short_history"] if start.year==2026 else [],"manager_change_date":"2025-03-03" if group=="active" else None})
    payload={"schema_version":1,"dataset_id":DATASET_ID,"dataset_version":DATASET_VERSION,"mode":"synthetic","source":"synthetic",
        "generator_version":GENERATOR_VERSION,"seed":seed,"as_of":AS_OF,"calendar_version":CALENDAR,"products":products,
        "family_count":len({p["fund_family_id"] for p in products}),"group_counts":dict((g,n) for g,n,*_ in GROUPS)}
    payload["fingerprint"]=fingerprint(payload)
    return payload
