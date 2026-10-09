"""Convert against bank-owned holdings and reuse precheck. No money/units held."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, localcontext
from ...models.wealth import WealthProductVersionORM
from ...schemas.wealth import InvestmentCommand
from .transfer_service import BankError
from .wealth_asset_summary import begin_read


async def convert(db, user_id, request, operation_id):
    from . import wealth_service as bank
    await begin_read(db)
    facts = await bank.assets(db,user_id)
    if request.context_id != facts['context_id']:
        raise BankError('资金环境已变化，请刷新持仓重新预览','STALE_CONTEXT',409)
    accounts=set(facts['asset_summary']['account_ids'])
    lots=[lot for lot in facts['lots'] if lot['account_id'] in accounts and
          f"{lot['account_id']}:{lot['product_id']}:{lot['product_version']}" == request.position_key]
    if not lots:
        raise BankError('当前范围没有该持仓，请明确选择本人的持仓','POSITION_NOT_FOUND',404)
    first=lots[0]
    version=await db.get(WealthProductVersionORM,first['version_id'])
    now=await bank.business_now(db,first['account_id'])
    price=await bank.latest_nav(db,version.version_id,now)
    if price is None:
        raise BankError('净值缺失，无法换算市值或生成可信赎回预览','DATA_STALE',409)
    holding=sum(bank.scaled(lot['units']) for lot in lots)
    available=sum(bank.scaled(lot['redeemable_units']) for lot in lots)
    with localcontext() as context:
        context.prec=48
        try:
            nav=Decimal(price.price)
        except (InvalidOperation, TypeError, ValueError):
            raise BankError('有效净值缺失','DATA_STALE',409)
        if not nav.is_finite() or nav<=0:
            raise BankError('有效净值缺失','DATA_STALE',409)
        if request.input_mode=='market_value':
            resolved=int((Decimal(request.market_value_fen)*100/nav).to_integral_value(rounding=ROUND_DOWN))
        elif request.input_mode=='holding_ratio':
            resolved=int((Decimal(holding)*Decimal(request.holding_ratio)).to_integral_value(rounding=ROUND_DOWN))
        else:
            resolved=bank.scaled(request.units)
        if resolved<=0:
            raise BankError('输入换算后不足0.0001份，请修改输入','INVALID_REDEMPTION_INPUT',422)
        if resolved>available:
            raise BankError(f'请求超过可赎{bank.quantity(available)}份；锁定和在途占用不会自动加入或缩减','INSUFFICIENT_REDEEMABLE_UNITS',409)
        market=int((Decimal(resolved)*nav/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        ratio=format(Decimal(resolved)/Decimal(holding),'.8f')
    command=InvestmentCommand(operation_id=operation_id,direction='redeem',account_id=first['account_id'],
        product_id=first['product_id'],product_version=first['product_version'],
        disclosure_version=version.disclosure['version'],amount_fen=0,units=bank.quantity(resolved),
        **({'command_version':2,'redemption_mode':'quick'} if request.redemption_mode=='quick' else {}))
    preview=await bank.precheck(db,user_id,command)
    if preview['estimated_nav'] != price.price or preview['estimated_nav_date'] != price.nav_date.isoformat():
        raise BankError('换算与预览之间行情已变化，请重新预览并确认','DATA_CHANGED',409)
    requested=request.market_value_fen if request.input_mode=='market_value' else market
    notice=f'{Decimal(requested)/100:.2f}元为拟赎回持仓的市值，不代表实际到账金额。实际到账金额以成交净值及扣除赎回费用后的结果为准。'
    return dict(input=request.model_dump(mode='json'),position_key=request.position_key,
        holding_units=bank.quantity(holding),redeemable_units=bank.quantity(available),resolved_units=command.units,
        estimated_market_value_fen=market,holding_ratio=ratio,nav=price.price,nav_date=price.nav_date.isoformat(),
        estimated_fee_fen=preview['estimate']['fee_fen'],estimated_net_cash_fen=preview['estimate']['cash_fen'],
        notice=notice,preview=preview)
