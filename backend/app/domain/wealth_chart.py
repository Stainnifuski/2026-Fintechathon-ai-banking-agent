"""Three descriptive NAV statistics, aligned to a calendar-month chart window."""
from calendar import monthrange
from datetime import date,datetime,timedelta
from decimal import Decimal,localcontext
from .time import BEIJING
from .wealth import business_day

METRIC_VERSION='nav-descriptive-v1'


def window_start(end:date,period:str)->date:
    months={'1m':1,'3m':3,'1y':12}.get(period)
    if months is None:raise ValueError('INVALID_CHART_RANGE')
    year,month=divmod(end.year*12+end.month-1-months,12)
    return date(year,month+1,min(end.day,monthrange(year,month+1)[1]))


def chart(history:list[dict],*,as_of:datetime,period:str,calendar_version:str,data_version:str,simulated:bool)->dict:
    if as_of.tzinfo is None:raise ValueError('AS_OF_TIMEZONE_REQUIRED')
    end=as_of.astimezone(BEIJING).date();start=window_start(end,period)
    visible=[]
    for row in history:
        day=date.fromisoformat(row['date'])
        if not start<=day<=end:continue
        if datetime.fromisoformat(row['available_at'])>as_of:continue
        value=Decimal(row['nav'])
        if not value.is_finite() or value<=0:raise ValueError('INVALID_NAV')
        # Carried display points are never a newly observed NAV/valuation day.
        if row.get('original_nav_date',row['date'])!=row['date'] or not business_day(day,calendar_version):continue
        visible.append({'date':row['date'],'nav':row['nav']})
    visible.sort(key=lambda p:p['date'])
    if len({p['date'] for p in visible})!=len(visible):raise ValueError('DUPLICATE_NAV_DATE')
    values=[Decimal(p['nav']) for p in visible]
    quality=[]
    if visible:
        observed={p['date'] for p in visible};first=date.fromisoformat(visible[0]['date']);last=date.fromisoformat(visible[-1]['date'])
        for offset in range((last-first).days+1):
            day=first+timedelta(days=offset)
            if business_day(day,calendar_version) and day.isoformat() not in observed:quality.append('MISSING_EXPECTED_VALUATION');break
    metrics={}
    def record(name,value=None,reason=None):
        metrics[name]={'value_percent':format(value.quantize(Decimal('0.01')),'.2f') if value is not None else None,
            'status':'AVAILABLE' if value is not None else 'UNAVAILABLE','reason':reason}
    with localcontext() as ctx:
        ctx.prec=40
        if len(values)>=2:
            record('nav_change',(values[-1]/values[0]-1)*100)
            peak=values[0];drawdown=Decimal(0)
            for value in values:
                peak=max(peak,value);drawdown=max(drawdown,1-value/peak)
            record('max_drawdown',drawdown*100)
        else:
            record('nav_change',reason='AT_LEAST_TWO_OBSERVATIONS');record('max_drawdown',reason='AT_LEAST_TWO_OBSERVATIONS')
        returns=[values[i]/values[i-1]-1 for i in range(1,len(values))]
        if any(abs(value)>Decimal('0.5') for value in returns):quality.append('ANOMALOUS_NAV_JUMP')
        if len(returns)<20:record('volatility',reason='AT_LEAST_TWENTY_RETURNS')
        elif quality:record('volatility',reason='DATA_QUALITY_REVIEW_REQUIRED')
        else:
            mean=sum(returns)/len(returns)
            variance=sum((r-mean)**2 for r in returns)/(len(returns)-1)
            record('volatility',variance.sqrt()*Decimal(252).sqrt()*100)
    return {'metric_version':METRIC_VERSION,'period':period,'requested_start':start.isoformat(),
        'window_start':visible[0]['date'] if visible else None,'window_end':visible[-1]['date'] if visible else None,
        'as_of':as_of.isoformat(),'as_of_nav_date':visible[-1]['date'] if visible else None,
        'observation_count':len(visible),'return_count':max(0,len(visible)-1),'metrics':metrics,'points':visible,
        'quality_flags':quality,'calendar_version':calendar_version,'data_version':data_version,'simulation':simulated,
        'notice':'单位净值描述统计，非含分红总回报、收益预测或客户账户收益。' if not simulated else '模拟净值描述统计，不代表真实产品历史或未来收益。'}
