"""Owned identity, proof-based links and annotation persistence. No ledger edits."""
import hashlib
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, delete, func, update, or_
from ...models.account import AccountORM
from ...models.user import UserORM
from ...models.transaction import TransactionORM as Tx
from ...models.bank_operation import BankOperationORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.merchant_refund import MerchantRefundORM
from ...models.bill_foundation import (
    BillFoundationLockORM as Lock, BillMerchantORM as Merchant, BillMerchantAliasORM as Alias,
    BillTransferPairORM as Pair, BillTransactionMetaORM as Meta, BillUserTagORM as Tag,
    BillTransactionTagORM as TxTag, BillMetadataEventORM as Event, BillRecurringStreamORM as Stream,
)
from .account_scope import isolated_account_ids
from .transfer_service import BankError


def key(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:32]


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).strip().casefold().split())


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def lock_users(db, user_ids):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    for user_id in sorted(set(user_ids)):
        await db.execute(insert(Lock).values(user_id=user_id).on_conflict_do_nothing(index_elements=['user_id']))
        await db.scalar(select(Lock).where(Lock.user_id == user_id).with_for_update())


async def account_ids(db, user_id):
    return list((await db.scalars(select(AccountORM.account_id).where(
        AccountORM.user_id == user_id, AccountORM.account_id.not_in(isolated_account_ids())))).all())


async def owned_transaction(db, user_id, tx_id):
    tx = await db.scalar(select(Tx).where(Tx.tx_id == tx_id, Tx.account_id.in_(await account_ids(db, user_id))))
    if tx is None:
        raise BankError('流水不存在或无权访问', 'TRANSACTION_NOT_FOUND', 404)
    return tx


async def merchant_registry(db, user_id, name, bank_identity=None):
    norm = normalize(name)
    if not norm:
        return None
    alias = await db.scalar(select(Alias).where(Alias.user_id == user_id, Alias.normalized_name == norm))
    verified = await db.scalar(select(Merchant).where(Merchant.user_id == user_id, Merchant.bank_identity == bank_identity, Merchant.merged_into_id.is_(None))) if bank_identity else None
    if alias:
        merchant = await db.get(Merchant, alias.merchant_id)
        # Explicit aliases are user decisions; never silently merge two known bank identities.
        if (bank_identity is None or merchant.bank_identity in (None, bank_identity)) and (verified is None or verified.merchant_id == merchant.merchant_id):
            if bank_identity and merchant.bank_identity is None:
                merchant.bank_identity = bank_identity
            return merchant
    merchant_id = key('M-', user_id + ':' + ('bank:' + bank_identity if bank_identity else 'text:' + norm))
    merchant = verified or await db.get(Merchant, merchant_id)
    if merchant and merchant.merged_into_id:
        merchant = await db.get(Merchant, merchant.merged_into_id)
    if merchant is None:
        merchant = Merchant(merchant_id=merchant_id, user_id=user_id, display_name=name, bank_identity=bank_identity)
        db.add(merchant)
        await db.flush()
    if alias is None:
        db.add(Alias(alias_id=key('A-', user_id + ':' + norm), user_id=user_id, name=name,
                     normalized_name=norm, merchant_id=merchant.merchant_id))
        await db.flush()
    return merchant


async def sync_users(db, user_ids, *, now=None, exact_pair=None, changed_tx_ids=None, incremental=False):
    """One transaction/lock order shared by backfill, ledger writers and metadata UI."""
    await db.flush()
    await lock_users(db, user_ids)
    result = {}
    for user_id in sorted(set(user_ids)):
        result[user_id] = await _sync_user(db, user_id, now=now, exact_pair=exact_pair, changed_tx_ids=changed_tx_ids, incremental=incremental)
    return result


async def _sync_user(db, user_id, *, now=None, exact_pair=None, changed_tx_ids=None, incremental=False):
    ids = await account_ids(db, user_id)
    bounded = incremental or changed_tx_ids is not None
    statement=select(Tx).where(Tx.account_id.in_(ids))
    if changed_tx_ids is not None:statement=statement.where(Tx.tx_id.in_(changed_tx_ids))
    elif incremental:statement=statement.outerjoin(Meta,Meta.tx_id==Tx.tx_id).where(Meta.tx_id.is_(None))
    transactions = list((await db.scalars(statement.order_by(Tx.timestamp, Tx.tx_id))).all())
    # Refund originals are loaded by a bank-issued payment link, never by a ledger-wide scan.
    from ...models.bill_features import MerchantRefundPartORM
    refund_queries=[]
    for refund_model in (MerchantRefundORM,MerchantRefundPartORM):
        stmt=select(refund_model,BankOperationORM,PaymentRequestPaymentORM).join(PaymentRequestPaymentORM,PaymentRequestPaymentORM.payment_request_id==refund_model.payment_request_id).join(BankOperationORM,BankOperationORM.operation_id==PaymentRequestPaymentORM.operation_id).where(refund_model.customer_account_id.in_(ids),refund_model.status=='SUCCEEDED')
        if bounded:stmt=stmt.where(refund_model.customer_tx_id.in_([tx.tx_id for tx in transactions]))
        refund_queries.extend((await db.execute(stmt)).all())
    originals=set(op.bank_tx_id for _,op,_ in refund_queries)-{tx.tx_id for tx in transactions}
    if originals:transactions.extend((await db.scalars(select(Tx).where(Tx.tx_id.in_(originals),Tx.account_id.in_(ids)))).all())
    by_id = {tx.tx_id: tx for tx in transactions}
    metas = {m.tx_id: m for m in (await db.scalars(select(Meta).where(Meta.tx_id.in_(by_id)))).all()}
    for tx in transactions:
        if tx.tx_id not in metas:
            metas[tx.tx_id] = Meta(tx_id=tx.tx_id, annotation_version=0)
            db.add(metas[tx.tx_id])
    await db.flush()
    ops = {o.operation_id: o for o in (await db.scalars(select(BankOperationORM).where(
        BankOperationORM.from_account.in_(ids), BankOperationORM.status == 'SUCCEEDED',
        *([or_(BankOperationORM.bank_tx_id.in_(by_id),BankOperationORM.operation_id.in_([t.operation_id for t in transactions if t.operation_id]))] if bounded else [])))).all()}
    counterpart_accounts = {a.account_id: a for a in (await db.scalars(select(AccountORM).where(
        AccountORM.account_id.in_([o.to_account for o in ops.values()])))).all()}
    bank_tx_ops={op.bank_tx_id:op for op in ops.values()}
    for tx in transactions:
        if tx.type not in ('purchase', 'subscription', 'salary') or metas[tx.tx_id].merchant_id:
            continue
        op = ops.get(tx.operation_id) or bank_tx_ops.get(tx.tx_id)
        bank_identity = None
        if tx.type == 'purchase' and op and op.bank_tx_id == tx.tx_id and op.to_account in counterpart_accounts:
            bank_identity = counterpart_accounts[op.to_account].user_id
        merchant = await merchant_registry(db, user_id, tx.counterparty, bank_identity)
        if merchant:
            metas[tx.tx_id].merchant_id = merchant.merchant_id
    await db.flush()
    # Only the original bank payment/refund chain is proof. Never match by text or amount alone.
    links = refund_queries
    for refund, op, payment in links:
        original, credit = by_id.get(op.bank_tx_id), by_id.get(refund.customer_tx_id)
        if (original and credit and original.type == 'purchase' and credit.type == 'refund'
            and original.account_id == credit.account_id == refund.customer_account_id
            and original.status == credit.status == 'completed' and op.status == 'SUCCEEDED'
            and 0 < refund.amount_fen == credit.amount_fen <= original.amount_fen == op.amount_fen
            and op.from_account == payment.payer_account_id == refund.customer_account_id
            and op.to_account == refund.merchant_account_id):
            meta = metas[credit.tx_id]
            meta.original_tx_id, meta.refund_source = original.tx_id, 'merchant_refund_part' if isinstance(refund,MerchantRefundPartORM) else 'merchant_refund'
            meta.merchant_id = metas[original.tx_id].merchant_id
    pairs = {p.operation_id: p for p in (await db.scalars(select(Pair).where(Pair.user_id == user_id))).all()}
    for op in ops.values():
        if op.operation_id in pairs or op.to_account not in ids:
            continue
        debit = by_id.get(op.bank_tx_id)
        if not debit or debit.type != 'transfer_out' or debit.status != 'completed' or debit.account_id != op.from_account:
            continue
        candidates = [t for t in transactions if t.type == 'transfer_in' and t.status == 'completed'
                      and t.account_id == op.to_account and t.counterparty == op.from_account
                      and t.amount_fen == debit.amount_fen == op.amount_fen
                      and utc(t.timestamp) == utc(debit.timestamp) and not metas[t.tx_id].transfer_pair_id]
        if exact_pair and exact_pair[0] == debit.tx_id:
            candidates = [t for t in candidates if t.tx_id == exact_pair[1]]
        if len(candidates) != 1 or debit.counterparty != op.to_account:
            continue
        credit = candidates[0]
        pair = Pair(pair_id=key('P-', op.operation_id), user_id=user_id, operation_id=op.operation_id,
                    debit_tx_id=debit.tx_id, credit_tx_id=credit.tx_id, source='bank_operation')
        db.add(pair)
        await db.flush()
        metas[debit.tx_id].transfer_pair_id = metas[credit.tx_id].transfer_pair_id = pair.pair_id
    await db.flush()
    from .bill_recurring import sync_recurring
    touched={(tx.account_id,metas[tx.tx_id].merchant_id) for tx in transactions if metas[tx.tx_id].merchant_id}
    recurring_tx=transactions;recurring_metas=metas
    if bounded:
        from .bill_schedules import TZ
        from .bill_service import shifted
        since=shifted(utc(now or datetime.now(timezone.utc)).astimezone(TZ).replace(day=1,hour=0,minute=0,second=0,microsecond=0),months=-36)
        recurring_tx=list((await db.scalars(select(Tx).join(Meta,Meta.tx_id==Tx.tx_id).where(Tx.account_id.in_(ids),Meta.merchant_id.in_([m for _,m in touched]),Tx.timestamp>=utc(since),Tx.type.in_(('purchase','salary','subscription'))))).all()) if touched else []
        recurring_metas={m.tx_id:m for m in (await db.scalars(select(Meta).where(Meta.tx_id.in_([t.tx_id for t in recurring_tx])))).all()}
    await sync_recurring(db, user_id, ids, recurring_tx, recurring_metas, now=now, touched=touched if bounded else None)
    return {'transactions': len(transactions), 'refund_links': sum(m.original_tx_id is not None for m in metas.values()),
            'paired_legs': sum(m.transfer_pair_id is not None for m in metas.values()),
            'merchant_links': sum(m.merchant_id is not None for m in metas.values())}


async def refresh_all(db, *, now=None):
    users = list((await db.scalars(select(UserORM.user_id).order_by(UserORM.user_id))).all())
    from ...models.bill_features import BillSyncStateORM as SyncState
    await lock_users(db,users)
    result={}
    for user_id in users:
        state=await db.get(SyncState,user_id,populate_existing=True)
        result[user_id]=await _sync_user(db,user_id,now=now,incremental=bool(state and state.revision>=4))
        if state:state.revision=4
        else:db.add(SyncState(user_id=user_id,revision=4))
    await db.flush()
    return result


async def metadata_views(db, user_id, tx_ids):
    if not tx_ids:
        return {}
    owned=await account_ids(db,user_id)
    rows = (await db.execute(select(Meta, Merchant).outerjoin(Merchant, Merchant.merchant_id == Meta.merchant_id)
                            .join(Tx, Tx.tx_id == Meta.tx_id).where(
        Meta.tx_id.in_(tx_ids), Tx.account_id.in_(owned)))).all()
    tag_rows = (await db.execute(select(TxTag.tx_id, Tag).join(Tag, Tag.tag_id == TxTag.tag_id).where(
        TxTag.tx_id.in_(tx_ids), Tag.user_id == user_id).order_by(Tag.name))).all()
    tags = defaultdict(list)
    for tx_id, tag in tag_rows:
        tags[tx_id].append({'tag_id': tag.tag_id, 'name': tag.name})
    original_ids = [meta.original_tx_id for meta, _ in rows if meta.original_tx_id]
    originals = {t.tx_id: t for t in (await db.scalars(select(Tx).where(Tx.tx_id.in_(original_ids), Tx.account_id.in_(owned)))).all()}
    refund_ids = defaultdict(list)
    for meta in (await db.scalars(select(Meta).join(Tx, Tx.tx_id == Meta.tx_id).where(Meta.original_tx_id.in_(tx_ids), Tx.account_id.in_(owned)))).all():
        refund_ids[meta.original_tx_id].append(meta.tx_id)
    refund_totals = dict((await db.execute(select(Meta.original_tx_id,func.sum(Tx.amount_fen)).join(Tx,Tx.tx_id==Meta.tx_id).where(Meta.original_tx_id.in_(tx_ids),Tx.account_id.in_(owned),Tx.status=='completed').group_by(Meta.original_tx_id))).all())
    from .bill_classification import classification_views
    categories=await classification_views(db,user_id,tx_ids)
    original_categories=await classification_views(db,user_id,original_ids) if original_ids else {}
    result = {}
    pair_ids = [meta.transfer_pair_id for meta, _ in rows if meta.transfer_pair_id]
    pairs = {p.pair_id: p for p in (await db.scalars(select(Pair).where(Pair.pair_id.in_(pair_ids), Pair.user_id == user_id))).all()}
    for meta, merchant in rows:
        pair = pairs.get(meta.transfer_pair_id)
        original = originals.get(meta.original_tx_id)
        result[meta.tx_id] = dict(**categories.get(meta.tx_id,{}), refunded_fen=int(refund_totals.get(meta.tx_id,0)), refundable_fen=None, original_category=('subscription' if original.type == 'subscription' else original.category) if original else None, refund_tx_ids=sorted(refund_ids[meta.tx_id]), merchant_id=meta.merchant_id, merchant_name=merchant.display_name if merchant else None,
            original_tx_id=meta.original_tx_id, refund_link_source=meta.refund_source,
            transfer_pair_id=meta.transfer_pair_id,
            paired_tx_id=(pair.credit_tx_id if meta.tx_id == pair.debit_tx_id else pair.debit_tx_id) if pair else None,
            recurring_stream_id=meta.recurring_stream_id, tags=tags[meta.tx_id], annotation_version=meta.annotation_version)
    purchases=(await db.scalars(select(Tx).where(Tx.tx_id.in_(tx_ids),Tx.account_id.in_(owned),Tx.type=='purchase'))).all()
    for tx in purchases:
        if tx.tx_id in result:result[tx.tx_id]['refundable_fen']=max(0,tx.amount_fen-result[tx.tx_id]['refunded_fen'])
    for meta,_ in rows:
        if meta.original_tx_id in original_categories:
            category=original_categories[meta.original_tx_id]['effective_category']
            result[meta.tx_id].update(original_category=category,effective_category=category,category_source='original_transaction')
    # Old, not-yet-synchronized rows still receive read-only category suggestions.
    for tx_id,category in categories.items():
        if tx_id not in result:result[tx_id]=category
    return result


async def list_tags(db, user_id):
    rows = (await db.execute(select(Tag, func.count(TxTag.tx_id)).outerjoin(TxTag, TxTag.tag_id == Tag.tag_id)
                            .where(Tag.user_id == user_id).group_by(Tag.tag_id).order_by(Tag.name))).all()
    return [dict(tag_id=t.tag_id, name=t.name, transaction_count=count) for t, count in rows]


async def annotate(db, user_id, tx_id, request):
    await lock_users(db, [user_id])
    await owned_transaction(db, user_id, tx_id)
    names = {normalize(name): name.strip() for name in request.tags}
    if '' in names or len(names) != len(request.tags):
        raise BankError('标签不能重复或只包含空白', 'INVALID_TAGS', 422)
    meta = await db.get(Meta, tx_id, populate_existing=True)
    if meta is None:
        meta = Meta(tx_id=tx_id, annotation_version=0)
        db.add(meta)
        await db.flush()
    before = (await metadata_views(db, user_id, [tx_id]))[tx_id]
    current_names = {normalize(t['name']) for t in before['tags']}
    if current_names == set(names):
        return before
    if meta.annotation_version != request.expected_version:
        raise BankError('标签已被其他操作修改，请刷新后重试', 'STALE_ANNOTATION', 409)
    await db.execute(delete(TxTag).where(TxTag.tx_id == tx_id))
    for norm, name in names.items():
        tag = await db.scalar(select(Tag).where(Tag.user_id == user_id, Tag.normalized_name == norm))
        if tag is None:
            tag = Tag(tag_id=key('T-', user_id + ':' + norm), user_id=user_id, name=name, normalized_name=norm)
            db.add(tag)
            await db.flush()
        db.add(TxTag(tx_id=tx_id, tag_id=tag.tag_id))
    meta.annotation_version += 1
    db.add(Event(event_id='E-'+uuid4().hex, user_id=user_id, action='transaction_tags', subject_id=tx_id,
                 before={'tags': before['tags']}, after={'tags': list(names.values()), 'version': meta.annotation_version}))
    await db.flush()
    return (await metadata_views(db, user_id, [tx_id]))[tx_id]


async def add_alias(db, user_id, merchant_id, name):
    await lock_users(db, [user_id])
    merchant = await db.get(Merchant, merchant_id)
    if not merchant or merchant.user_id != user_id or merchant.merged_into_id:
        raise BankError('商户不存在或无权访问', 'MERCHANT_NOT_FOUND', 404)
    norm = normalize(name)
    if not norm:
        raise BankError('别名不能为空白', 'INVALID_ALIAS', 422)
    alias = await db.scalar(select(Alias).where(Alias.user_id == user_id, Alias.normalized_name == norm))
    if alias and alias.merchant_id != merchant_id:
        raise BankError('别名已属于另一商户，请使用明确的商户归并', 'ALIAS_CONFLICT', 409)
    if alias is None:
        db.add(Alias(alias_id=key('A-', user_id+':'+norm), user_id=user_id, name=name.strip(), normalized_name=norm, merchant_id=merchant_id))
        db.add(Event(event_id='E-'+uuid4().hex,user_id=user_id,action='merchant_alias',subject_id=merchant_id,before={},after={'alias':name.strip()}))
    await db.flush()
    return {'merchant_id': merchant_id, 'name': name.strip()}


async def merge_merchants(db, user_id, target_id, source_id, preview_digest=None):
    from .bill_merchants import merge
    return await merge(db,user_id,target_id,source_id,preview_digest)


async def catalog(db, user_id):
    merchants = list((await db.scalars(select(Merchant).where(Merchant.user_id == user_id, Merchant.merged_into_id.is_(None)).order_by(Merchant.display_name))).all())
    aliases = defaultdict(list)
    for alias in (await db.scalars(select(Alias).where(Alias.user_id == user_id).order_by(Alias.name))).all():
        aliases[alias.merchant_id].append(alias.name)
    ids = await account_ids(db, user_id)
    totals = int(await db.scalar(select(func.count()).select_from(Tx).where(Tx.account_id.in_(ids))) or 0)
    linked = int(await db.scalar(select(func.count()).select_from(Meta).join(Tx,Tx.tx_id==Meta.tx_id).where(Tx.account_id.in_(ids))) or 0)
    return {'merchants': [dict(merchant_id=m.merchant_id, name=m.display_name, aliases=aliases[m.merchant_id],
                               bank_verified=m.bank_identity is not None) for m in merchants],
            'tags': await list_tags(db, user_id), 'transactions_total': totals, 'metadata_total': linked,
            'notices': ['商户目录、别名和标签仅属于当前用户，不修改原始流水或其他人的统计。',
                        '归并会合并同一商户的统计；不同已核实银行身份不能归并。',
                        '历史缺少唯一付款证据的退款或互转保持未关联，不按姓名或近似金额猜测。']}
