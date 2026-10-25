"""NOVA bill discovery and preference preview tools. No execution tool is exposed."""
from typing import Literal
from pydantic import Field
from ...schemas.nova import StrictModel
from ...schemas.nova_bills import PrepareBillChange
from ...schemas.bill_features import SplitSpec
from ..bank import bill_foundation, bill_classification, bill_merchants, nova_bills
from ..bank.transfer_service import BankError


class CatalogArguments(StrictModel):
    section: Literal['merchants','tags','category_rules','merchant_history']
    name: str | None = Field(default=None, min_length=1, max_length=64,
        description='可选精确名称筛选，商户也匹配其别名；不传则分页列出。')
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=30)


class MerchantPreviewArguments(StrictModel):
    mode: Literal['merge','split']
    merchant_id: str = Field(min_length=1, max_length=40)
    source_merchant_id: str | None = Field(default=None, min_length=1, max_length=40)
    split: SplitSpec | None = None
    page: int = Field(default=1, ge=1, le=10000)


async def catalog(arguments, context):
    from .nova_tools import envelope
    if arguments.section in {'merchants','tags'}:
        result = await bill_foundation.catalog(context.db, context.user_id)
        rows = result[arguments.section]
    elif arguments.section == 'category_rules':
        rows = await bill_classification.rules(context.db, context.user_id)
        names = {r['merchant_id']:r['name'] for r in (await bill_foundation.catalog(context.db, context.user_id))['merchants']}
        rows = [{**r, 'name':names.get(r['merchant_id'], '已归并商户')} for r in rows]
    else:
        rows = await bill_merchants.history(context.db, context.user_id)
    if arguments.name:
        rows = [r for r in rows if arguments.name in [r.get('name'), r.get('target_name'), r.get('source_name'), *r.get('aliases', [])]]
    total = len(rows)
    start = (arguments.page - 1) * arguments.page_size
    return envelope('bills.catalog', dict(section=arguments.section, items=rows[start:start+arguments.page_size],
        total=total, page=arguments.page, has_more=start+arguments.page_size<total,
        notices=['商户变更记录限最近50项。'] if arguments.section == 'merchant_history' else []))


async def merchant_preview(arguments, context):
    from .nova_tools import envelope
    db, user_id = context.db, context.user_id
    if arguments.mode == 'merge':
        if not arguments.source_merchant_id or arguments.split:
            raise BankError('归并需明确两个商户，不能传拆分参数', 'MISSING_SELECTION', 422)
        data = await bill_merchants.preview(db, user_id, arguments.merchant_id, arguments.source_merchant_id)
    else:
        if arguments.source_merchant_id:
            raise BankError('拆分不接受归入商户参数', 'INVALID_ARGUMENTS', 422)
        data = await bill_merchants.split_preview(db, user_id, arguments.merchant_id, arguments.split)
        state = await bill_merchants.state(db, user_id, arguments.merchant_id)
        from ..bank.nova_reads import transaction_detail
        ids = state['tx_ids'][(arguments.page-1)*20:arguments.page*20]
        data.update(name=state['name'], items=[await transaction_detail(db, user_id, key) for key in ids],
                    page=arguments.page, total=len(state['tx_ids']), has_more=arguments.page*20<len(state['tx_ids']))
        if arguments.split:
            data['new_name'] = arguments.split.name
    return envelope('bills.merchant_preview', {**data, 'mode':arguments.mode, 'executed':False})


async def prepare(arguments, context):
    from .nova_tools import envelope
    if not context.session_id or not context.request_id or not context.original_message:
        raise BankError('生成账单确认卡需要有效对话', 'BILL_CONTEXT_REQUIRED', 422)
    if arguments.request_quote not in context.original_message:
        raise BankError('修改依据必须逐字引用本轮用户原文', 'PARAMETER_NOT_GROUNDED', 422)
    selected = context.page.selected.id if context.page.selected else None
    try:
        card = await nova_bills.prepare(context.db, context.user_id, context.session_id,
            context.request_id, arguments, selected)
    except Exception:
        await context.db.rollback()
        raise
    return envelope('bills.preference_preview', {'card':card, 'executed':card['status']=='completed'})


def bill_tools():
    entries = [
        ('get_bill_catalog', CatalogArguments, catalog,
         '分页查询本人商户/别名、标签、商户分类规则或最近50项归并拆分历史。name精确匹配名称或别名；多个同名先询问。标签ID、商户ID、规则版本均从本工具取得。'),
        ('preview_bill_merchant_change', MerchantPreviewArguments, merchant_preview,
         '只读预览本人商户归并/拆分，取得范围摘要和preview_digest，不执行。拆分可先不传split分页查所属流水，再传明确的新名称、所选流水和别名得到绑定参数的预览摘要。'),
        ('prepare_bill_change', PrepareBillChange, prepare,
         '用户明确要求修改账单整理或预测设置时，生成15分钟有效的本人确认卡，尚不修改。支持标签完整替换、分类及商户规则、月度预算、异常核对、周期计划与预测开关、商户别名/归并/拆分/撤销、保存基准预测、刷新历史关联。不转账不退款不取消扣费。先查询取得真实对象、版本和异常指纹；归并/拆分先预览并原样传digest。不明确则追问，不能猜对象。聊天“确认”不执行；用户在卡片点击确认。金额单位分。'),
    ]
    return {name:{'schema':{'type':'function','function':{'name':name,'description':description,
                    'parameters':model.model_json_schema()}},
                  'arguments':model,'execute':execute,'requires_identity':True}
            for name,model,execute,description in entries}
