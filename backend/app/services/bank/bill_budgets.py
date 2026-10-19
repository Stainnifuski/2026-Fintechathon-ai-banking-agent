import calendar
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from ...models.bill_features import BillBudgetORM as Budget
from ...models.bill_foundation import BillMetadataEventORM as Event
from ...schemas.bills import BillQuery
from ...schemas.bill_insights import CashflowScenario
from .bill_foundation import key,lock_users,account_ids
from .bill_service import analyze,TZ,aware,shifted
from .bill_explanation import rounded
from .transfer_service import BankError


def validate_month(month):
    try:
        at=datetime.strptime(month,'%Y-%m')
        if at.strftime('%Y-%m')!=month or not 2000<=at.year<=2099:raise ValueError()
        return at.replace(tzinfo=TZ)
    except ValueError:raise BankError('月份无效','INVALID_BUDGET_MONTH',422) from None


async def save(db,user_id,body):
    validate_month(body.month)
    await lock_users(db,[user_id])
    if body.account_id and body.account_id not in await account_ids(db,user_id):raise BankError('账户不存在','ACCOUNT_NOT_FOUND',404)
    budget_id=key('B-',f'{user_id}:{body.month}:{body.account_id}:{body.category}')
    row=await db.get(Budget,budget_id,populate_existing=True)
    if (row.version if row else 0)!=body.expected_version:raise BankError('预算已更新，请刷新','STALE_BUDGET',409)
    before={'amount_fen':row.amount_fen,'active':row.active} if row else {}
    if row is None:
        row=Budget(budget_id=budget_id,user_id=user_id,month=body.month,account_id=body.account_id,category=body.category,amount_fen=body.amount_fen,warning_percent=body.warning_percent,active=body.active,version=1);db.add(row)
    else:row.amount_fen=body.amount_fen;row.warning_percent=body.warning_percent;row.active=body.active;row.version+=1
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='budget',subject_id=budget_id,before=before,after={'amount_fen':body.amount_fen,'active':body.active}))
    await db.flush();return {'budget_id':budget_id,'version':row.version}


async def summary(db,user_id,month,*,now=None):
    from .bill_cashflow import project_cashflow
    from ...models.bill_features import BillRecurringScheduleORM as Schedule
    from ...models.bill_foundation import BillRecurringStreamORM as Stream
    start=validate_month(month);end=shifted(start,months=1)
    now=aware(now or datetime.now(timezone.utc)).astimezone(TZ)
    rows=(await db.scalars(select(Budget).where(Budget.user_id==user_id,Budget.month==month).order_by(Budget.budget_id))).all()
    reports={};forecasts={};items=[]
    from ...models.bill_features import BillCategoryRuleORM as Rule
    stream_categories={sid:category for sid,category in (await db.execute(select(Stream.stream_id,Schedule.category).join(Schedule,Schedule.stream_id==Stream.stream_id).where(Stream.user_id==user_id))).all()}
    for sid,category in (await db.execute(select(Stream.stream_id,Rule.category).join(Rule,Rule.merchant_id==Stream.merchant_id).where(Stream.user_id==user_id,Rule.user_id==user_id,Stream.source!='manual'))).all():stream_categories[sid]=category
    for row in rows:
        scope=row.account_id
        if scope and scope not in await account_ids(db,user_id):continue
        if scope not in reports:
            reports[scope]=await analyze(db,user_id,BillQuery(date=month,account_id=scope),now=max(now,start))
        report=reports[scope]
        raw_net=report['totals']['net_consumption_fen'] if row.category is None else next((c['net_amount_fen'] for c in report['categories'] if c['category']==row.category),0)
        spent=max(0,raw_net);remaining=row.amount_fen-spent;forecast=spent;complete=end<=now
        if start<=now<end:
            if scope not in forecasts:
                try:forecasts[scope]=await project_cashflow(db,user_id,CashflowScenario(days=min(32,max(1,(end.date()-now.date()).days+1)),account_id=scope),now=now)
                except BankError as error:
                    if error.code!='CASH_ACCOUNT_REQUIRED':raise
                    forecasts[scope]=None
            projection=forecasts[scope]
            if projection:
                horizon=Decimal(str((end-now).total_seconds()))/86400
                for baseline in projection['spending_baselines']:
                    if row.category is None or baseline['category']==row.category:
                        forecast+=rounded(Decimal(baseline['estimated_total_fen'])*horizon/projection['days'])
                for event in projection['events']:
                    if event['at']>=end or event['amount_fen']>=0 or event['source'] not in ('subscription_plan','recurring_estimate','recurring_plan'):continue
                    category='subscription' if event['source']=='subscription_plan' else stream_categories.get(event['reference_id'],'other')
                    if row.category is None or row.category==category:forecast-=event['amount_fen']
                complete=bool(projection['spending_baselines'])
        usage=round(spent*100/row.amount_fen,2)
        status='disabled' if not row.active else 'exceeded' if remaining<0 else 'warning' if usage>=row.warning_percent else 'projected_exceeded' if forecast>row.amount_fen else 'ok'
        items.append(dict(budget_id=row.budget_id,month=month,account_id=scope,category=row.category,amount_fen=row.amount_fen,warning_percent=row.warning_percent,active=row.active,version=row.version,
            spent_fen=spent,raw_net_spending_fen=raw_net,remaining_fen=remaining,usage_percent=usage,projected_spending_fen=forecast,projection_has_variable_baseline=complete,status=status))
    return {'month':month,'as_of':now,'items':items,'notices':['总预算与分类预算分开核对，不相加；预算不包含转账与理财。','到账退款抵减当期净消费，负净消费的预算占用按0计。','本月预测使用周期计划和可变消费样本；样本不足时仅为已知部分估计，预算提醒仅在应用内展示。']}
