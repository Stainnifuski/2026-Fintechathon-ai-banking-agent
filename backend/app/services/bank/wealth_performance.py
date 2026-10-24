"""Whole investment performance, separate from product NAV performance.

Modified Dietz weights actual purchase/redemption cash flows by elapsed time.
No annualization, interpolation, guessed prices or persistent balance copies.
"""
from calendar import monthrange
from datetime import datetime,time,timedelta
from decimal import Decimal,ROUND_HALF_UP
from ...domain.time import BEIJING
from .wealth_returns import Replay,build


def shift(day,months):
    index=day.year*12+day.month-1+months
    year,month=divmod(index,12);month+=1
    return day.replace(year=year,month=month,day=min(day.day,monthrange(year,month)[1]))


def measure(replay,ids,start,end):
    opening=[replay.snapshot(pid,start,opening=True)[0] for pid in ids]
    closing=[replay.snapshot(pid,end)[0] for pid in ids]
    if any(v is None for v in [*opening,*closing]):return None,None
    begin=datetime.combine(start,time(),BEIJING)
    finish=datetime.combine(end+timedelta(days=1),time(),BEIJING)
    flows=[e for e in replay.events if e['pid'] in ids and begin<=e['at']<finish]
    net=sum(e['input']-e['output'] for e in flows)
    profit=sum(closing)-sum(opening)-net
    duration=Decimal(str((finish-begin).total_seconds()))
    capital=Decimal(sum(opening))+sum((Decimal(e['input']-e['output'])*
        Decimal(str((finish-e['at']).total_seconds()))/duration for e in flows),Decimal(0))
    rate=(Decimal(profit)*100/capital).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP) if capital>0 else None
    return profit,str(rate) if rate is not None else None


def overview(replay,query,scope):
    replay.published_as_of_now=True
    today=replay.now.date();end=today-timedelta(days=1)
    earliest=min((e['at'].date() for e in replay.events),default=end)
    requested=end if query.period=='day' else shift(today,-1) if query.period=='month' else shift(today,-12) if query.period=='year' else earliest
    start=min(end,max(requested,earliest))
    result=build(replay,query,scope,(start,end))
    ids=sorted(replay.errors)
    amount,rate=measure(replay,ids,start,end)
    points=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset)
        _,value=measure(replay,ids,start,day)
        points.append({'date':day.isoformat(),'rate_percent':value})
    notice=('收益记录暂时无法核对，已有资金和持仓不受影响。' if any(replay.errors.values()) else
        '部分净值尚未更新，收益暂不可用。' if amount is None else '')
    positions=[]
    if query.period=='day':
        orders,lots,settlements,versions,navs,now=replay.source
        for account,version in sorted({(l.account_id,l.version_id) for l in lots if l.units_scaled>0}):
            selected=[o for o in orders if o.account_id==account and o.version_id==version]
            ops={o.operation_id for o in selected}
            subset=Replay(selected,[l for l in lots if l.account_id==account and l.version_id==version],
                [s for s in settlements if s.operation_id in ops],versions,navs,now)
            subset.published_as_of_now=True
            pid=subset.versions[version].product_id
            pnl,_=measure(subset,[pid],end,end)
            positions.append({'position_key':f'{account}:{version}', 'yesterday_pnl_fen':pnl})
    result['performance']={'period':query.period,'start_date':start.isoformat(),'end_date':end.isoformat(),
        'amount_fen':amount,'rate_percent':rate,'points':points,'notice':notice,
        'positions':positions,'rate_method':'modified-dietz-nonannualized-v1','as_of':replay.now.isoformat()}
    return result
