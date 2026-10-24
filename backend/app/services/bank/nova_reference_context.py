"""Owner-scoped references to lists actually returned by NOVA, with a short TTL."""
import json
from datetime import datetime,timezone,timedelta
from ...models.nova_conversation import NovaConversationORM
from ...models.nova_reference_context import NovaReferenceContextORM
from .nova_conversations import _encrypt,_decrypt,ConversationError

TTL=timedelta(minutes=15)
PROVIDERS={'list_cards','list_subscriptions','list_transactions','list_calendar_events',
           'search_calendar_events','list_shop_products','list_shop_orders'}
FIELDS={'card_id','card_number','subscription_id','merchant_name','tx_id','event_id','title',
        'product_id','order_id','name','product_version'}


async def load(db,user,session):
    conversation=await db.get(NovaConversationORM,session)
    if not conversation or conversation.user_id!=user:raise ConversationError()
    row=await db.get(NovaReferenceContextORM,session)
    if not row or row.user_id!=user:return []
    now=datetime.now(timezone.utc)
    result=[]
    for item in json.loads(_decrypt(row.payload_ciphertext)):
        if now-datetime.fromisoformat(item['saved_at'])<TTL:result.append(item)
    return result


async def save(db,user,session,trace):
    old=await load(db,user,session)
    retained={item['name']:item for item in old}
    changed=False
    for item in trace:
        name=item.get('name')
        if name not in PROVIDERS:continue
        result=item.get('result') or {}
        # A latest failed lookup invalidates the previous list; never fall back
        # to old success. Keep failure in the snapshot to force clarification.
        raw=(result.get('data') or {}).get('items',[])
        rows=[{k:v for k,v in row.items() if k in FIELDS and isinstance(v,(str,int))}
              for row in raw[:10] if isinstance(row,dict)] if isinstance(raw,list) else []
        retained.pop(name,None)
        retained[name]={'name':name,'saved_at':datetime.now(timezone.utc).isoformat(),
            'result':{'error':result.get('error'), 'shop':result.get('shop'),
                      'data':{'items':rows}}}
        changed=True
    if not changed:return
    row=await db.get(NovaReferenceContextORM,session)
    payload=_encrypt(json.dumps(list(retained.values()),ensure_ascii=False))
    if row:row.payload_ciphertext=payload
    else:db.add(NovaReferenceContextORM(conversation_id=session,user_id=user,payload_ciphertext=payload))
    # Caller commits with the corresponding visible conversation turn.
