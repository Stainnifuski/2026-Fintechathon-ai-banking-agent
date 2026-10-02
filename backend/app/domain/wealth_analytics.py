"""确定性研究指标；十进制结果，无法计算时返回原因而非补零。"""
from datetime import date, timedelta
from decimal import Decimal, localcontext

FORMULA_VERSION="analytics-v12-1"


def value(number: Decimal | None) -> str | None:
    return format(number.quantize(Decimal("0.000001")),"f") if number is not None else None


def window(history: list[dict], as_of: str, period: str="1y") -> list[dict]:
    end=date.fromisoformat(as_of)
    start=end-timedelta(days={"1m":30,"3m":90,"1y":365}.get(period,100000))
    return [p for p in history if start.isoformat()<=p["date"]<=as_of]


def metrics(history: list[dict], *, as_of: str, period: str="1y", dividends_verified: bool=False) -> dict:
    rows=window(history,as_of,period)
    meta={"formula_version":FORMULA_VERSION,"as_of":as_of,"frequency":"business_daily","annual_days":252,
          "samples":len(rows),"start":rows[0]["date"] if rows else None,"end":rows[-1]["date"] if rows else None,
          "basis":"verified_dividend_reinvestment" if dividends_verified else "unit_nav_change_not_total_return"}
    if len(rows)<2: return {**meta,"unavailable_reason":"INSUFFICIENT_HISTORY","return":None,"annualized":None,"max_drawdown":None,"volatility":None}
    with localcontext() as ctx:
        ctx.prec=36
        prices=[Decimal(r["nav"]) for r in rows]
        daily=[(prices[i]+(Decimal(rows[i].get("dividend_per_unit","0")) if dividends_verified else 0))/prices[i-1]-1 for i in range(1,len(rows))]
        index=[Decimal(1)]
        for ret in daily: index.append(index[-1]*(1+ret))
        peak=index[0]; drawdown=Decimal(0)
        for p in index:
            peak=max(peak,p); drawdown=max(drawdown,1-p/peak)
        days=(date.fromisoformat(rows[-1]["date"])-date.fromisoformat(rows[0]["date"])).days
        annual=index[-1]**(Decimal(365)/days)-1 if days>=365 else None
        mean=sum(daily)/len(daily)
        variance=sum((r-mean)**2 for r in daily)/(len(daily)-1) if len(daily)>=20 else None
        vol=variance.sqrt()*Decimal(252).sqrt() if variance is not None else None
        return {**meta,"return":value(index[-1]-1),"annualized":value(annual),"max_drawdown":value(drawdown),"volatility":value(vol),
                "unavailable_reason":None,"annualized_unavailable_reason":None if annual is not None else "LESS_THAN_ONE_YEAR",
                "volatility_unavailable_reason":None if vol is not None else "LESS_THAN_20_RETURNS",
                "missing_days_policy":"observed_pairs_only_no_forward_fill","quality_flags":["price_return_may_include_dividend_drop"] if not dividends_verified else []}


def correlation(left: list[dict],right: list[dict]) -> dict:
    a={p["date"]:Decimal(p["nav"]) for p in left}; b={p["date"]:Decimal(p["nav"]) for p in right}
    days=sorted(set(a)&set(b))
    if len(days)<31: return {"correlation":None,"samples":len(days),"unavailable_reason":"INSUFFICIENT_COMMON_OBSERVATIONS"}
    x=[a[d]/a[p]-1 for p,d in zip(days,days[1:])]; y=[b[d]/b[p]-1 for p,d in zip(days,days[1:])]
    mx=sum(x)/len(x); my=sum(y)/len(y)
    sx=sum((i-mx)**2 for i in x); sy=sum((i-my)**2 for i in y)
    if not sx or not sy: return {"correlation":None,"samples":len(x),"unavailable_reason":"ZERO_VARIANCE"}
    return {"correlation":value(sum((i-mx)*(j-my) for i,j in zip(x,y))/(sx*sy).sqrt()),"samples":len(x),"unavailable_reason":None}


def comparison(products: list[dict],as_of: str,period: str="1y",amount_fen: int=100000,holding_days: int=365) -> dict:
    from .wealth import purchase, advance_business
    histories=[window(p["history"],as_of,period) for p in products]
    common=sorted(set.intersection(*[{r["date"] for r in h} for h in histories])) if histories else []
    rows=[]
    for p,h in zip(products,histories):
        data=[r for r in h if r["date"] in set(common)]
        rules=p.get("rules",{})
        fee=None
        if data and rules:
            charge=purchase(amount_fen,data[-1]["nav"],rules["purchase_fee_rate"])
            band=next((r["rate"] for r in rules["redeem_fee_bands"] if holding_days<r["days_lt"]),None)
            fee={"purchase_fen":charge.fee_fen,"redemption_rate":band,"annual_fees_in_nav":rules.get("annual_fees_in_nav",True)}
        rows.append({"product_id":p["product_id"],"name":p["name"],"fund_family_id":p.get("fund_family_id"),
            "metrics":metrics(data,as_of=as_of,period=period,dividends_verified=p.get("source")=="synthetic"),"fees":fee,
            "cash_date_if_redeem_today":advance_business(date.fromisoformat(as_of),rules.get("cash_business_days",0),rules.get("calendar_version","synthetic-weekdays-v1")).isoformat() if rules else None,
            "dataset_key":p["dataset_key"],"source":p["source"],"history":data})
    return {"as_of":as_of,"period":period,"amount_fen":amount_fen,"holding_days":holding_days,"common_samples":len(common),"products":rows,
            "correlations":[{"left":products[i]["product_id"],"right":products[j]["product_id"],**correlation(histories[i],histories[j])} for i in range(len(products)) for j in range(i+1,len(products))]}


def concentration(holdings: list[dict]) -> dict:
    total=sum(h["amount_fen"] for h in holdings); families={}; categories={}
    for h in holdings:
        family=h.get("fund_family_id") or "unknown:"+h["product_id"]
        families[family]=families.get(family,0)+h["amount_fen"]
        categories[h["category"]]=categories.get(h["category"],0)+h["amount_fen"]
    return {"total_fen":total,"families_fen":families,"categories_fen":categories,
            "overconcentrated_families":[k for k,v in families.items() if total and v*100>total*60],
            "underlying_exposure":None,"unavailable_reason":"NO_VERIFIED_UNDERLYING_HOLDINGS",
            "notice":"A/C同家族合并；当前权重历史假设不等于实际持仓收益，现金流不能当作投资回报"}


def hypothetical_portfolio(products: list[dict], weights_fen: dict[str,int], as_of: str, period: str='1y') -> dict:
    """Fixed initial allocations and units, no invented rebalancing or actual cash-flow return."""
    total=sum(weights_fen.values())
    if total<=0 or any(type(v) is not int or v<0 for v in weights_fen.values()): raise ValueError('INVALID_PORTFOLIO_WEIGHTS')
    by_id={p['product_id']:p for p in products}
    missing=[pid for pid,v in weights_fen.items() if v and (pid not in by_id or not by_id[pid]['history'])]
    if missing: return {'metrics':None,'missing_products':missing,'unavailable_reason':'MISSING_COMPONENT_HISTORY'}
    components=[by_id[pid] for pid,v in weights_fen.items() if v]
    histories=[window(p['history'],as_of,period) for p in components]
    common=sorted(set.intersection(*[set(r['date'] for r in h) for h in histories])) if histories else []
    if len(common)<2: return {'metrics':None,'unavailable_reason':'INSUFFICIENT_COMMON_OBSERVATIONS'}
    normalized={p['product_id']:{r['date']:Decimal(r['nav']) for r in h} for p,h in zip(components,histories)}
    series=[{'date':day,'nav':str(sum(Decimal(weights_fen[pid])/total*prices[day]/prices[common[0]] for pid,prices in normalized.items()))} for day in common]
    return {'metrics':metrics(series,as_of=as_of,period=period),'history':series,'weights_fen':weights_fen,
        'basis':'fixed_initial_units_unit_nav_hypothesis_not_actual_portfolio_return','as_of':as_of,'datasets':sorted({p['dataset_key'] for p in components}),
        'notice':'假设起点固定投入与份额；不计申赎费用和后续现金流，不是实际持仓收益；分红未复权'}
