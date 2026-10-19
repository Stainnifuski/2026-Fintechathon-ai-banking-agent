"""Explicit user rules and explainable suggestions; never rewrite original categories."""
from collections import defaultdict, Counter
from sqlalchemy import select, case, func
from ...models.transaction import TransactionORM as Tx
from ...models.bill_foundation import BillTransactionMetaORM as Meta, BillMetadataEventORM as Event
from ...models.bill_features import BillClassificationORM as Correction, BillCategoryRuleORM as Rule
from .bill_foundation import account_ids, owned_transaction, lock_users, key
from .transfer_service import BankError
from uuid import uuid4

CATEGORIES = ('dining','shopping','transport','entertainment','subscription','education','other')
WORDS = {'education':('学费','课程','教材','书店','学习','培训'),'dining':('餐','外卖','食堂','咖啡','奶茶'), 'transport':('交通','地铁','公交','滴滴','出租','高铁','机票'),
         'entertainment':('电影','游戏','音乐','影院','乐园'), 'shopping':('商城','购物','商店','京东','淘宝','超市')}


def consumption_category(tx=Tx):
    correction = select(Correction.category).where(Correction.tx_id == tx.tx_id).correlate(tx).scalar_subquery()
    rule = select(Rule.category).join(Meta,Meta.merchant_id==Rule.merchant_id).where(Meta.tx_id==tx.tx_id).correlate(tx).scalar_subquery()
    return case((tx.type=='subscription','subscription'), else_=func.coalesce(correction,rule,
        case((tx.category.in_(CATEGORIES),tx.category),else_='other')))


async def classification_views(db,user_id,tx_ids):
    ids=await account_ids(db,user_id)
    rows=(await db.execute(select(Tx,Meta,Correction,Rule).outerjoin(Meta,Meta.tx_id==Tx.tx_id)
        .outerjoin(Correction,Correction.tx_id==Tx.tx_id).outerjoin(Rule,Rule.merchant_id==Meta.merchant_id)
        .where(Tx.tx_id.in_(tx_ids),Tx.account_id.in_(ids)))).all()
    merchant_ids={m.merchant_id for _,m,_,_ in rows if m and m.merchant_id}
    historical=defaultdict(Counter)
    for merchant,category,count in (await db.execute(select(Meta.merchant_id,Correction.category,func.count())
        .join(Tx,Tx.tx_id==Meta.tx_id).join(Correction,Correction.tx_id==Tx.tx_id)
        .where(Meta.merchant_id.in_(merchant_ids),Tx.account_id.in_(ids),Correction.category.is_not(None))
        .group_by(Meta.merchant_id,Correction.category))).all():
        historical[merchant][category]=count
    result={}
    for tx,meta,correction,rule in rows:
        raw=tx.category
        effective=('subscription' if tx.type=='subscription' else raw) if tx.type!='purchase' else correction.category if correction and correction.category else rule.category if rule else raw if raw in CATEGORIES else 'other'
        suggestion=None; reason=None
        if tx.type=='purchase':
            counts=historical[meta.merchant_id] if meta else Counter()
            if counts and counts.most_common(1)[0][1]>=2 and counts.most_common(1)[0][1]/sum(counts.values())>=.8:
                suggestion=counts.most_common(1)[0][0];reason='同商户至少2笔本人修正记录中，80%以上属于该分类'
            if not suggestion:
                matches=[cat for cat,words in WORDS.items() if any(word in tx.counterparty for word in words)]
                if len(matches)==1:suggestion=matches[0];reason='商户名称关键词匹配，需本人核对'
        result[tx.tx_id]={'raw_category':raw,'effective_category':effective,
            'category_source':'transaction_override' if correction and correction.category else 'merchant_rule' if rule and tx.type=='purchase' else 'original',
            'classification_version':correction.version if correction else 0, 'category_rule_version':rule.version if rule else 0,
            'suggested_category':suggestion if suggestion!=effective else None,'suggestion_reason':reason if suggestion!=effective else None}
    return result


async def correct(db,user_id,tx_id,body):
    await lock_users(db,[user_id]);tx=await owned_transaction(db,user_id,tx_id)
    if tx.type!='purchase':raise BankError('仅消费流水支持分类纠错，订阅与退款按业务关联分类','CATEGORY_NOT_EDITABLE',422)
    row=await db.get(Correction,tx_id,populate_existing=True)
    if (row.version if row else 0)!=body.expected_version:raise BankError('分类已更新，请刷新','STALE_CLASSIFICATION',409)
    if body.apply_to_merchant and body.category is None:raise BankError('沿用规则需指定分类','CATEGORY_REQUIRED',422)
    meta=await db.get(Meta,tx_id)
    if not meta or not meta.merchant_id:
        from .bill_foundation import sync_users
        await sync_users(db,[user_id],changed_tx_ids=[tx_id]);meta=await db.get(Meta,tx_id)
    rule=await db.get(Rule,meta.merchant_id) if meta and meta.merchant_id else None
    if body.apply_to_merchant and (rule.version if rule else 0)!=body.expected_rule_version:
        raise BankError('商户分类规则已更新，请刷新','STALE_CATEGORY_RULE',409)
    before=(await classification_views(db,user_id,[tx_id]))[tx_id]
    if row is None:row=Correction(tx_id=tx_id,category=body.category,version=1);db.add(row)
    else:row.category=body.category;row.version+=1
    if body.apply_to_merchant:
        if not meta or not meta.merchant_id:raise BankError('缺少商户标识，无法保存规则','MERCHANT_REQUIRED',422)
        if rule:rule.category=body.category;rule.version+=1
        else:db.add(Rule(merchant_id=meta.merchant_id,user_id=user_id,category=body.category,version=1))
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='classification',subject_id=tx_id,
        before=before,after={'category':body.category,'apply_to_merchant':body.apply_to_merchant}))
    await db.flush();return (await classification_views(db,user_id,[tx_id]))[tx_id]


async def rules(db,user_id):
    return [dict(merchant_id=r.merchant_id,category=r.category,version=r.version) for r in (await db.scalars(select(Rule).where(Rule.user_id==user_id))).all()]


async def remove_rule(db,user_id,merchant_id,version):
    await lock_users(db,[user_id]);row=await db.get(Rule,merchant_id,populate_existing=True)
    if not row or row.user_id!=user_id:raise BankError('规则不存在','CATEGORY_RULE_NOT_FOUND',404)
    if row.version!=version:raise BankError('规则已更新，请刷新','STALE_CATEGORY_RULE',409)
    db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='remove_category_rule',subject_id=merchant_id,before={'category':row.category},after={}))
    await db.delete(row);await db.flush();return {'removed':True}
