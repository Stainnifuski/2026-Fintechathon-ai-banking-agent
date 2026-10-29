"""Non-authorizing step dependencies and rollback proposals for NOVA wealth."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation

STAGES = ('match','cash_management','stable','growth','candidates','selected','budget',
          'amounts','purchase_review','allocation','details','redemption_input','trade')
LABELS = dict(zip(STAGES, ('匹配条件','活钱候选','稳健候选','进取候选','产品候选','已选产品',
    '规划资金','申购金额','申购核对','配置建议','交易明细','赎回输入','本人确认与验证')))


def sequence(kind):
    if kind == 'planning':
        return ['match','cash_management','stable','growth','selected','budget','allocation','details','trade']
    if kind in {'redeem','positions'}:return ['redemption_input','trade']
    return ['match','candidates','selected','amounts','purchase_review','trade']


def effective_kind(data):
    """Use the user's explicit card workflow while it belongs to this request."""
    kind=data['command']['kind']
    view=data.get('view_context') or {}
    if (kind in {'recommend','purchase'} and view.get('workflow')=='planning'
            and view.get('workflow_request_id')==data.get('request_id')):
        return 'planning'
    return kind


def parameter_stage(prior,command,fallback=None):
    """Dependencies come from actual changed parameters, not a model's stage claim."""
    old=prior.get('command') or {};view=prior.get('view_context') or {}
    def number(v):
        try:return Decimal(v) if v not in (None,'') else None
        except (InvalidOperation,TypeError):return v
    def ids(items):return {(p['product_id'],p['product_version']) for p in items}
    stages=[];kind=command['kind']
    if command.get('category')!=old.get('category') or command.get('name_query')!=old.get('name_query'):stages.append('match')
    if ids(command.get('products',[]))!=ids(view.get('selected_products',old.get('products',[]))):
        stages.append('selected' if kind=='planning' else 'redemption_input' if kind=='redeem' else 'candidates')
    field=lambda f:'invest' if f=='investment' else f
    if kind=='planning' and (number(command.get('cash_yuan'))!=number(view.get('cash_yuan',old.get('cash_yuan')))
            or field(command.get('cash_field'))!=field(view.get('cash_field',old.get('cash_field')))):
        stages.append('budget')
    if kind in {'recommend','purchase'}:
        before=view.get('amounts_yuan') or {p['product_id']:v for p,v in zip(old.get('products',[]),old.get('amounts_yuan',[]))}
        if any(number(v)!=number(before.get(p['product_id'])) for p,v in zip(command.get('products',[]),command.get('amounts_yuan',[]))):stages.append('amounts')
    if kind=='redeem' and any(command.get(k)!=old.get(k) for k in ('redemption_value','redemption_mode','redemption_input')):stages.append('redemption_input')
    order=sequence(kind)
    return next((s for s in order if s in stages),fallback)


def affected(kind, stage):
    steps=sequence(kind)
    if stage not in steps:raise ValueError('当前任务没有这个步骤')
    # The candidate pools are independent siblings. Changing one does not erase
    # the choices in the other pools, but invalidates their shared successors.
    if stage in {'cash_management','stable','growth'}:
        return [stage,*steps[steps.index('selected'):]]
    return steps[steps.index(stage):]


def reset_data(data, stage, command=None):
    result=deepcopy(data)
    kind=effective_kind(data)
    invalid=affected(kind,stage)
    view=result.setdefault('view_context',{})
    view['completed_stages']=[s for s in view.get('completed_stages',[]) if s not in invalid]
    result.pop('instruction',None)
    result.pop('execution_plan_id',None)
    result.pop('pending_backtrack',None)
    result['draft_epoch']=result.get('draft_epoch',0)+1
    result['reopen_stage']=stage
    if command is not None:result['command']=deepcopy(command)
    cmd=result['command']
    if stage=='match':
        view.update(selected_products=[],visible_products=[],amounts_yuan={})
        cmd.update(products=[],amounts_yuan=[])
        if command is None:cmd.update(category=None,name_query=None,name_query_quote=None)
    elif command is not None:
        view['selected_products']=deepcopy(cmd.get('products',[]))
    elif stage=='candidates':
        view['selected_products']=[]
        cmd['products']=[]
    elif stage in {'cash_management','stable','growth'}:
        buckets={p['product_id']:p.get('bucket') for p in view.get('visible_products',[])}
        remaining=[p for p in view.get('selected_products',[]) if p.get('bucket',buckets.get(p['product_id']))!=stage]
        view['selected_products']=remaining
        cmd['products']=[{k:v for k,v in p.items() if k!='bucket'} for p in remaining]
    cmd['auto_prepare']=False
    if 'amounts' in invalid:
        view['amounts_yuan']={}
        cmd['amounts_yuan']=[None for _ in cmd.get('products',[])]
    if stage=='amounts' and command is not None:
        view['amounts_yuan']={p['product_id']:v or '' for p,v in zip(cmd.get('products',[]),command.get('amounts_yuan',[]))}
        cmd['amounts_yuan']=deepcopy(command.get('amounts_yuan',[]))
    if 'budget' in invalid:
        view.update(cash_field=None,cash_yuan='')
        cmd.update(cash_field=None,cash_yuan=None)
    if stage=='budget' and command is not None:
        cmd.update(cash_field=command.get('cash_field'),cash_yuan=command.get('cash_yuan'))
        view.update(cash_field='investment' if cmd['cash_field']=='invest' else cmd['cash_field'],cash_yuan=cmd['cash_yuan'] or '')
    result['command']=cmd
    return result
