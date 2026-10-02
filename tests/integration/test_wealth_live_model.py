"""Opt-in real DeepSeek calls, isolated databases; never counted as offline success."""
import os
import json
from pathlib import Path
from uuid import uuid4
import pytest
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.services.bank import wealth_research as research,wealth_service as bank
from backend.app.models.wealth_research import WealthContextORM

pytestmark=pytest.mark.skipif(os.getenv('WEALTH_LIVE_TEST')!='true',reason='未启用真实DeepSeek付费联调')


@pytest.mark.asyncio
async def test_real_deepseek_continuous_dialogue_and_citations(store,http_store,monkeypatch):
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    client,_,_=http_store; evidence=[]; session=None
    async with store() as db:
        ctx=await research.initialize(db,'U1'); await db.commit(); await research.select_context(db,'U1',ctx['context_id'],1)
    async def send(text):
        nonlocal session
        response=await client.post('/api/v1/wealth/messages',json={'message':text,'client_message_id':uuid4().hex,**(session or {})})
        assert response.status_code==200,response.text
        reply=response.json(); evidence.append({'input':text,'reply':reply})
        session={'session_id':reply['session_id'],'version':reply['version']}
        return reply
    try:
        reply=await send('我有两万元，半年后可能要用，帮我看看怎么安排。')
        assert reply['kind']=='clarification' and '保留' in reply['message'],reply
        reply=await send('留五千，剩下的尽量稳一点。')
        assert reply['kind']=='assessment_required',reply
        async with store() as db: await assess(db)
        plan=await send('继续刚才的配置')
        assert plan['kind']=='plan' and len(plan['plan']['items'])>=2,plan
        compared=await send('第二个和第一个有什么区别？')
        assert compared['kind']=='comparison' and compared['analysis']['common_samples']>30,compared
        bought=await send('那第二个买五千。')
        assert bought['kind']=='command' and bought['command']['amount_fen']==500000,bought
        old=(await client.post('/api/v1/wealth/previews',json=bought['command'])).json()
        changed=await send('改成三千。')
        assert changed['kind']=='command' and changed['command']['amount_fen']==300000,changed
        rejected=await client.post(f"/api/v1/wealth/previews/{old['authorization_id']}/confirm",json={'digest':old['digest'],'disclosure_version':1,'accepted':True})
        assert rejected.status_code==409
        view=(await client.post('/api/v1/wealth/previews',json=changed['command'])).json()
        aid=view['authorization_id']
        confirmed=await client.post(f'/api/v1/wealth/previews/{aid}/confirm',json={'digest':view['digest'],'disclosure_version':1,'accepted':True}); assert confirmed.status_code==200,confirmed.text
        verified=await client.post(f'/api/v1/wealth/previews/{aid}/verify/demo'); assert verified.status_code==200,verified.text
        accepted=await client.post(f'/api/v1/wealth/previews/{aid}/execute'); assert accepted.status_code==200,accepted.text
        status=await send('刚才那笔成功了吗？')
        assert status['kind']=='order' and status['order']['status']=='ACCEPTED',status
        pending=await send('把它赎回一半。')
        assert pending['kind']=='clarification' and '计价' in pending['message'],pending
        async with store() as db:
            context=await db.get(WealthContextORM,ctx['context_id']); context.data={**context.data,'business_at':'2026-10-05T19:00:00+08:00'}; await db.commit()
            settled=await bank.settle(db,changed['command']['operation_id']); assert settled['status']=='SETTLED'
        redeem=await send('把它赎回一半。')
        assert redeem['kind']=='command' and redeem['command']['direction']=='redeem',redeem
        knowledge=await send('为什么同一家基金的A和C不能算两份分散投资？')
        assert knowledge['kind']=='knowledge' and knowledge['citations'],knowledge
        assert all('同家族' in c['excerpt'] for c in knowledge['citations']),knowledge
        missing=await send('火星上的紫外融资比例是多少？')
        assert missing['kind']=='knowledge' and not missing.get('citations') and '资料不足' in missing['message'],missing
    finally:
        target=os.getenv('WEALTH_LIVE_EVIDENCE')
        if target:
            p=Path(target+'-'+str(store.kw.get('bind','db')) if False else target); p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
