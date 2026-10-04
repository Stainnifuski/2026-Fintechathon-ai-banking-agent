"""Exactly three opt-in live multi-turn scripts; isolated ledger, never mock NLU.

Run with WEALTH_SEMANTIC_LIVE=1 and -k sqlite. No keys or model context logged.
"""
import json
import os
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,assess,command,authorize,move
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthOrderORM
from backend.app.services.ai import wealth_model
from backend.app.services.bank import wealth_service as bank

SCRIPTS={
    'original_failure':[
        '申购MF002一千元','投资规划建议','规划现有资金','投入5成','投入10000',
        '我有2万半年后用','兼顾增长和波动','没有特别用途不用额外留现金',
        '改八千不要混合','先查持仓'],
    'unseen_corrections':[
        '拨一万元做投资，整整六个月内不会动它',
        '拿不准，你先列一些注明条件的选择',
        '不是追求快涨，改成尽量少起伏；不是立即要用，但周转时不能拖过两工作日',
        '刚才限制到账速度的要求撤掉，期限照旧',
        '先比较MF001和MF002，不要替我买'],
    'conditional_sell_buy':[
        '先查我的持仓',
        '先把MF001全部赎回，等这笔卖出款实际到手，拿其中一半重新配置；整整六个月内不会动它，收益和起伏折中',
        '现在先给我看看资产，不要执行任何交易'],
}


@pytest.mark.skipif(os.getenv('WEALTH_SEMANTIC_LIVE')!='1',reason='opt-in real DeepSeek calls')
@pytest.mark.parametrize('script',list(SCRIPTS))
@pytest.mark.asyncio
async def test_live_semantic_script(store,http_store,monkeypatch,script):
    client,_,_=http_store
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    async with store() as db:
        await assess(db)
        account=await db.get(AccountORM,'A1');account.balance_fen=1922900
        await db.commit()
        if script=='conditional_sell_buy':
            cmd=command('live-fixture-buy',product='MF001')
            aid=await authorize(db,cmd)
            await bank.accept_many(db,'U1',[(cmd,aid)])
            await move(db,12)
            assert (await bank.settle(db,cmd.operation_id))['status']=='SETTLED'
    replies=[];session={}
    try:
        for text in SCRIPTS[script]:
            response=await client.post('/api/v1/wealth/messages',json={
                'message':text,'client_message_id':uuid4().hex,**session})
            assert response.status_code==200,response.text
            result=response.json()
            replies.append(result);session.update(session_id=result['session_id'],version=result['version'])
            print(json.dumps({'script':script,'turn':len(replies),'kind':result['kind'],
                'code':result.get('code'),'message':result.get('message'),'intent':result.get('intent'),
                'requirements':result.get('requirement_card'),'model':result.get('model_evidence')},ensure_ascii=False))
            assert result['kind']!='model_unavailable',result
        if script=='original_failure':
            assert replies[0]['kind']=='command'
            assert replies[3]['requirement_card']['fields']['investment_budget']['resolved_fen']==961450
            assert replies[5]['requirement_card']['fields']['investment_budget']['resolved_fen']==1000000
            assert replies[5]['requirement_card']['fields']['horizon']['value']['count']==6
            assert replies[-2]['plan']['request']['budget_fen']==800000
            assert replies[-1]['kind']=='assets'
        elif script=='unseen_corrections':
            assert replies[1]['conditional']
            fields=replies[2]['requirement_card']['fields']
            assert fields['investment_goal']['value']=='reduce_volatility'
            assert fields['liquidity_requirement']['value']['max_business_days']==2
            assert fields['horizon']['value']['count']==6
            assert replies[3]['requirement_card']['fields']['liquidity_requirement']['state']=='unmentioned'
            assert replies[-1]['kind']=='comparison'
        else:
            assert replies[1]['kind']=='command',replies[1]
            assert replies[1]['task']['goal']=='sell_then_buy'
            assert any(n['kind']=='BUY_PLAN' for n in replies[1]['task']['nodes'])
            budget=replies[1]['requirement_card']['fields']['investment_budget']
            assert budget['value']['base']=='settled_proceeds' and budget['state']=='base_pending'
            assert replies[-1]['kind']=='assets'
        async with store() as db:
            assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==(1 if script=='conditional_sell_buy' else 0)
    finally:
        if wealth_model._client:
            await wealth_model._client.aclose();wealth_model._client=None
