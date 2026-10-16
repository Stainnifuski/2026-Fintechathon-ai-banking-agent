"""Structured adapters over existing handlers; no NLP, trading or new engine."""
from decimal import Decimal
from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace
from sqlalchemy import select
from ...schemas.wealth_action import WealthActionResultV1
from ...schemas.wealth import InvestmentCommand
from ...schemas.wealth_redemption import RedemptionRequest
from ...models.wealth import WealthConversationORM, WealthTaskORM
from ...models.wealth_research import WealthChatRequestORM
from ...integrations import wealth_authorization as policy
from ...domain.wealth_synthetic import fingerprint
from ...domain.time import utc_now

READ_ACTIONS=['list_orders','list_positions','show_wealth_returns','show_product_detail','compare_products']

def reject(message,code='INVALID_SELECTION',status=409):
    raise policy.WealthPolicyError(message,code,status)
def quantity(scaled):return f'{scaled//10000}.{scaled%10000:04d}'
def positions(facts):
    grouped={}
    for lot in facts['lots']:
        key=f"{lot['account_id']}:{lot['product_id']}:{lot['product_version']}"
        grouped.setdefault(key,[]).append(lot)
    rows=[]
    for key,lots in sorted(grouped.items()):
        first=lots[0]
        def total(field):return sum(int(Decimal(x[field])*10000) for x in lots)
        def known(field):return None if any(x.get(field) is None for x in lots) else sum(x[field] for x in lots)
        value=known('market_value_fen');basis=facts['asset_summary']['total_assets_fen']
        same=all(x.get('nav')==first.get('nav') and x.get('nav_date')==first.get('nav_date') for x in lots)
        rows.append(dict(object_type='positions',position_key=key,account_id=first['account_id'],product_id=first['product_id'],
            product_version=first['product_version'],product_name=first['product_name'],disclosure_version=first.get('disclosure_version'),
            share_class=first.get('share_class'),lot_ids=[x['lot_id'] for x in lots],lots=lots,
            units=quantity(total('units')),redeemable_units=quantity(total('redeemable_units')),
            held_units=quantity(total('held_units')),locked_units=quantity(total('locked_units')),
            market_value_fen=value,unrealized_pnl_fen=known('unrealized_pnl_fen'),cost_fen=sum(x['cost_fen'] for x in lots),
            total_ratio=str(Decimal(value)/Decimal(basis)) if basis and value is not None else None,
            nav=first.get('nav') if same else None,nav_date=first.get('nav_date') if same else None,
            daily_metric=first.get('daily_metric') if same else None))
    return rows

async def dispatch(db,user_id,bank,request,handlers=None,portfolio_source=None):
    # External bank scope is always checked before replay or object lookup.
    facts=await bank.assets(user_id)
    if facts['context_id']!=request.context_id:reject('资金范围已变化，请刷新','STALE_CONTEXT')
    key='WA'+fingerprint([user_id,request.request_id]);digest=fingerprint(request.model_dump())
    old=await db.get(WealthChatRequestORM,key)
    if old:
        if old.digest!=digest:reject('请求编号被用于不同参数','IDEMPOTENCY_CONFLICT')
        row=await db.get(WealthConversationORM,old.response['session_id'],populate_existing=True)
        if not row or row.user_id!=user_id or row.data.get('closed'):reject('会话已关闭','SESSION_CLOSED')
        return old.response
    if request.session_id:
        row=await db.get(WealthConversationORM,request.session_id,populate_existing=True)
        if not row or row.user_id!=user_id:reject('会话不存在','NOT_FOUND',404)
        if row.data.get('closed'):reject('会话已关闭','SESSION_CLOSED')
        if row.data.get('context_id','legacy-v1')!=request.context_id:reject('会话资金范围已变化','STALE_CONTEXT')
        if row.version!=request.expected_session_version:reject('会话版本已变化，请刷新后重试','STALE_SESSION')
    else:
        row=WealthConversationORM(session_id='WC'+uuid4().hex,user_id=user_id,version=1,
            data={'context_id':request.context_id,'history':[],'structured_actions':True})
        db.add(row)
        await db.commit()  # Session identity must be visible to the A access check.
    # Remote bank reads may persist exact-code NAV observations in a separate
    # transaction. Do not keep a SQLite writer lock or an uncommitted session
    # insert while waiting for them. Recheck replay/session under the user lock
    # immediately before persisting the action result.
    row=SimpleNamespace(session_id=row.session_id,version=row.version,data=deepcopy(row.data))
    await db.rollback()
    p=request.params;action=request.action;selection=request.selection_context
    held=positions(facts)
    catalog=None
    async def products():
        nonlocal catalog
        if catalog is None:
            codes=([ref.rsplit(':',1)[0] for ref in selection.ids] if selection and selection.object_type=='products' else
                   p.get('product_ids') if action=='compare_products' else [p['product_id']] if action=='show_product_detail' and p.get('product_id') else None)
            if codes is not None and hasattr(bank,'selected_products'):catalog=await bank.selected_products(user_id,codes)
            elif hasattr(bank,'call'):catalog=await bank.call('POST','/catalog',{'subject_user_id':user_id,'facts_only':True,'policy_references':True})
            else:catalog=await bank.products(user_id)
        return catalog
    if selection:
        if selection.object_type=='orders':valid={x['operation_id'] for x in facts['orders']}
        elif selection.object_type=='positions':valid={x['position_key'] for x in held}
        else:valid={f"{x['product_id']}:{x['version']}" for x in await products()}
        if not set(selection.ids)<=valid:reject('选择对象不属于当前范围或版本','OBJECT_SCOPE_MISMATCH')
    data=None;status='ok';message='查询完成';required=[];allowed=READ_ACTIONS.copy()
    def clarify(field,reason,options=None):
        nonlocal status,message
        status='clarify';message=reason;required.append(dict(field=field,reason=reason,options=options))
    def match(kind,ids):
        if selection and (selection.object_type!=kind or set(selection.ids)!=set(ids)):
            reject('参数对象与已选对象不一致','SELECTION_MISMATCH')
    if action in {'list_orders','list_positions'}:
        if selection and selection.object_type!=('orders' if action=='list_orders' else 'positions'):
            reject('订单与持仓的选择不能混用','SELECTION_MISMATCH')
        if p['page'] is None:clarify('page','请选择页码')
        if p['page_size'] is None:clarify('page_size','请选择每页行数')
        if not required:
            orders=[{**x,'object_type':'orders'} for x in facts['orders']]
            orders.sort(key=lambda x:x['operation_id']);orders.sort(key=lambda x:x.get('accepted_at') or '',reverse=True)
            items=orders if action=='list_orders' else held
            total=len(items);pages=max(1,(total+p['page_size']-1)//p['page_size']);page=min(p['page'],pages)
            data=dict(object_type='orders' if action=='list_orders' else 'positions',scope_key=facts['asset_summary']['asset_scope_id'],
                rows=items[(page-1)*p['page_size']:page*p['page_size']],page=page,page_size=p['page_size'],pages=pages,total=total)
            # Shared complete tables page the full authorized snapshot locally,
            # preserving independent cross-page selections. Existing paged fields
            # remain compatible; no second bank read or per-card narrowed scope.
            data['table_data'] = dict(object_type=data['object_type'],scope_key=data['scope_key'],rows=items)
            data['snapshot_id'] = facts['asset_summary']['snapshot_id']
    elif action=='show_wealth_returns':
        if selection:
            if selection.object_type!='positions':reject('收益入口需要持仓对象','SELECTION_MISMATCH')
            ids={x['product_id'] for x in held if x['position_key'] in selection.ids}
            if p['scope']=='product' and ids!={p['product_id']}:reject('收益产品与选择不一致','SELECTION_MISMATCH')
        data=await bank.call('POST','/returns',{'subject_user_id':user_id,**p})
        if data['asset_scope_id']!=facts['asset_summary']['asset_scope_id']:
            reject('资产授权范围已变化，请刷新后重新查询','STALE_CONTEXT')
    elif action=='show_product_detail':
        if selection:
            if len(selection.ids)!=1:reject('产品详情需要明确一个对象','SELECTION_MISMATCH')
            if selection.object_type=='positions':
                chosen=next(x for x in held if x['position_key']==selection.ids[0])
                selected_id,selected_version=chosen['product_id'],chosen['product_version']
            elif selection.object_type=='orders':
                chosen=next(x for x in facts['orders'] if x['operation_id']==selection.ids[0])
                selected_id,selected_version=chosen['product_id'],chosen['product_version']
            else:
                chosen=next(x for x in await products() if f"{x['product_id']}:{x['version']}"==selection.ids[0])
                selected_id,selected_version=chosen['product_id'],chosen['version']
            if (p['product_id'] is not None and p['product_id']!=selected_id) or (p['product_version'] is not None and p['product_version']!=selected_version):
                reject('产品详情参数与所选对象不一致','SELECTION_MISMATCH')
            p={**p,'product_id':selected_id,'product_version':selected_version}
        if not p['product_id'] or p['product_version'] is None:
            clarify('product_id','请选择明确产品及版本', [{'id':f"{x['product_id']}:{x['version']}",'label':x['name']} for x in await products()])
        else:
            data=await bank.call('POST','/product-detail',{'subject_user_id':user_id,**p})
    elif action=='compare_products':
        if not p['product_ids']:clarify('product_ids','请选择2至4只不同产品')
        else:
            rows=[x for x in await products() if x['product_id'] in p['product_ids']]
            if len(rows)!=len(p['product_ids']):reject('部分产品不存在','PRODUCT_UNAVAILABLE',404)
            if selection:match('products',[f"{x['product_id']}:{x['version']}" for x in rows])
            data=rows
    elif action=='preview_order_cancellation':
        ids=p['operation_ids'] or (selection.ids if selection and selection.object_type=='orders' else None)
        if not ids:clarify('operation_ids','请选择要核对撤单资格的订单')
        else:
            match('orders',ids)
            if not set(ids)<={x['operation_id'] for x in facts['orders']}:reject('订单不属于当前范围','OBJECT_SCOPE_MISMATCH')
            data={'items':[await bank.call('POST',f'/orders/{oid}/cancellation-eligibility',{'subject_user_id':user_id}) for oid in ids]}
            message='仅查询撤单资格；仍须在原接口本人确认取消'
    elif action=='preview_redemption':
        draft=p['redemption']
        if draft and draft['context_id']!=request.context_id:reject('赎回资金范围不一致','STALE_CONTEXT')
        ids=selection.ids if selection and selection.object_type=='positions' else []
        if selection and selection.object_type!='positions':reject('赎回只能选择持仓','SELECTION_MISMATCH')
        target=(draft or {}).get('position_key')
        if target:match('positions',[target])
        elif len(ids)==1:target=ids[0]
        else:clarify('redemption.position_key','请选择具体持仓；多只持仓需分别填写市值，不能自动均摊',
                     [{'id':x['position_key'],'label':x['product_name']+' · '+x['account_id']} for x in held if not ids or x['position_key'] in ids])
        mode=(draft or {}).get('input_mode');field={'units':'units','market_value':'market_value_fen','holding_ratio':'holding_ratio'}.get(mode)
        if not field or (draft or {}).get(field) is None:clarify('redemption.input','请明确输入方式和赎回数量或市值')
        if not required:
            if target not in {x['position_key'] for x in held}:reject('持仓不属于当前范围','OBJECT_SCOPE_MISMATCH')
            account=next(x['account_id'] for x in held if x['position_key']==target)
            tasks=list(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id==user_id,WealthTaskORM.account_id==account,
                WealthTaskORM.status.not_in(['COMPLETED','CANCELLED','PARTIAL_COMPLETED','FAILED']))))
            if any(t.control_mode!='AGENT' or t.status=='PAUSED' for t in tasks):
                status='blocked';message='该账户存在人工接管或暂停任务，请先处理原任务';data={'code':'TASK_CONTROL_BLOCKED','reason':message}
            else:
                normalized=RedemptionRequest.model_validate({**draft,'position_key':target})
                data=await bank.call('POST','/redemptions/preview',{**normalized.model_dump(mode='json'),
                    'subject_user_id':user_id,'operation_id':'WO'+fingerprint([user_id,request.request_id])[:32]})
                auth=await policy.new_preview_authorization(db,user_id,InvestmentCommand.model_validate(data['preview']['command']))
                data['preview']={**data['preview'],'authorization_id':auth.authorization_id,'authorization_status':auth.status,
                    'expires_at':policy.aware(auth.expires_at).isoformat()}
                message='赎回预览已生成；尚未本人确认或提交'
    elif action=='prepare_purchase':
        for field in ('portfolio_id','expected_version','snapshot_id'):
            if p[field] is None:clarify(field,'请先选择当前整体配置版本')
        if not required:
            if portfolio_source is None:
                status='blocked';message='整体配置服务尚未接入';data={'code':'PORTFOLIO_PROVIDER_UNAVAILABLE','reason':message}
            else:
                from .wealth_selection import prepare
                from ...schemas.wealth_selection import PortfolioPrepare
                data=await prepare(db,user_id,p['portfolio_id'],PortfolioPrepare(expected_version=p['expected_version'],snapshot_id=p['snapshot_id'],payment_account_id=p.get('payment_account_id')),bank,portfolio_source)
    else:
        if action=='recommend_products' and p['filter'] is None:clarify('filter','请选择普通、类别或主题推荐')
        if action=='revise_portfolio':
            if p['selection'] is None:clarify('selection','请先填写产品和整体比例')
            elif p['selection']['context_id']!=request.context_id:reject('配置选择范围不一致','STALE_CONTEXT')
            elif selection:match('products',[f"{x['product_id']}:{x['product_version']}" for x in p['selection']['items']])
        if selection and action in {'plan_portfolio','recommend_products'}:reject('此动作不消费已选对象','SELECTION_MISMATCH')
        if not required:
            handler=(handlers or {}).get(action)
            if handler is None:
                status='blocked';message='队友的配置/推荐服务尚未接入，此入口不返回固定产品';data={'code':'PORTFOLIO_PROVIDER_UNAVAILABLE','reason':message}
            else:
                db.info['wealth_portfolio_action_session']=row.session_id
                try:data=await handler(db=db,user_id=user_id,context_id=request.context_id,bank=bank,params=p)
                finally:db.info.pop('wealth_portfolio_action_session',None)
    current=(await bank.call('POST','/contexts',{'subject_user_id':user_id}))['selected']
    if current!=request.context_id:reject('处理期间资金环境已变化，请重新查询','STALE_CONTEXT')
    await policy.lock_profile(db,user_id)
    replay=await db.get(WealthChatRequestORM,key,populate_existing=True)
    if replay:
        if replay.digest!=digest:reject('请求编号被用于不同参数','IDEMPOTENCY_CONFLICT')
        replay_session=await db.get(WealthConversationORM,replay.response['session_id'],populate_existing=True)
        if not replay_session or replay_session.user_id!=user_id or replay_session.data.get('closed'):reject('会话已关闭','SESSION_CLOSED')
        result=deepcopy(replay.response)
        await db.rollback()
        return result
    stored=await db.get(WealthConversationORM,row.session_id,populate_existing=True)
    if stored is None:
        if request.session_id:reject('会话已关闭','SESSION_CLOSED')
        stored=WealthConversationORM(session_id=row.session_id,user_id=user_id,version=row.version,data=row.data)
        db.add(stored)
    else:
        if stored.user_id!=user_id or stored.data.get('closed'):reject('会话已关闭','SESSION_CLOSED')
        if stored.data.get('context_id','legacy-v1')!=request.context_id:reject('会话资金范围已变化','STALE_CONTEXT')
        if stored.version!=row.version:reject('会话版本已变化，请刷新后重试','STALE_SESSION')
    row=stored
    row.version+=1
    result=WealthActionResultV1(request_id=request.request_id,context_id=request.context_id,session_id=row.session_id,
        session_version=row.version,action=action,status=status,message=message,data=data,required_fields=required,allowed_actions=allowed).model_dump(mode='json')
    row.data={**row.data,'last_action_result':result}
    db.add(WealthChatRequestORM(request_key=key,user_id=user_id,digest=digest,status='COMPLETED',response=result,started_at=utc_now()))
    if action=='preview_redemption' and status=='ok':
        from .wealth_task import create_command
        await create_command(db,user_id,row.session_id,request.context_id,
            InvestmentCommand.model_validate(data['preview']['command']),holding_redemption=True)
    await db.commit()
    return result
