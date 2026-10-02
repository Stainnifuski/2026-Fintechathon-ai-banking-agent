"""离线有限意图与银行事实解释；无真实模型调用、无写工具权限。"""

import re
from decimal import Decimal, ROUND_UP
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...integrations.wealth_authorization import WealthPolicyError, require_suitability
from ...models.wealth import WealthConversationORM
from ..orchestration import wealth_plan
from ...domain.wealth_allocation import candidates

READ_TOOLS = ("wealth_products", "wealth_compare", "wealth_assets", "wealth_diagnose")
PREVIEW_TOOLS = ("wealth_purchase_preview", "wealth_redemption_preview", "wealth_allocation_preview")
# 验证、授权、受理、结算、时钟不进入模型白名单。
TOOL_PARAMETERS = {
    "wealth_products": {}, "wealth_assets": {}, "wealth_diagnose": {},
    "wealth_compare": {"product_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3}},
    "wealth_purchase_preview": {"product_id": {"type": "string"}, "amount_fen": {"type": "integer", "minimum": 1}, "account_id": {"type": "string"}},
    "wealth_redemption_preview": {"product_id": {"type": "string"}, "units": {"type": "string", "pattern": r"^\d+\.\d{4}$"}, "account_id": {"type": "string"}},
    "wealth_allocation_preview": {"budget_fen": {"type": "integer", "minimum": 0}, "reserve_fen": {"type": "integer", "minimum": 0}, "horizon_days": {"type": "integer", "minimum": 1}},
}
TOOL_SCHEMAS = [{"name": name, "strict": True, "description": "仅读取或生成待确认模拟预览，不执行交易",
    "parameters": {"type": "object", "properties": params, "required": list(params),
                   "additionalProperties": False}} for name, params in TOOL_PARAMETERS.items()]


def parse_money(value: str) -> int:
    """有限中文金额：一至十、两和百/千/万；明确标注非通用语言理解。"""
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    suffix = value[-1] if value[-1] in "百千万" else ""
    core = value[:-1] if suffix else value
    amount = Decimal(digits[core]) if core in digits else Decimal(core)
    fen = amount*{"": 100, "百": 10000, "千": 100000, "万": 1000000}[suffix]
    if fen != fen.to_integral_value() or not 0 <= fen <= 2_000_000_000:
        raise ValueError("金额必须精确到分且在模拟上限内")
    return int(fen)


def checked_explanation(proposal: dict, facts: list[dict]) -> str:
    """模型适配点：拒绝非事实产品、金额或状态；解释失败用确定性模板。"""
    allowed = {row["product_id"] for row in facts}
    if set(proposal) != {"product_ids", "reason_key"} or not isinstance(proposal["product_ids"], list):
        raise ValueError("解释输出schema无效")
    if not set(proposal["product_ids"]) <= allowed or proposal["reason_key"] not in {"risk", "fees", "liquidity"}:
        raise ValueError("解释必须来自服务端事实与理由白名单")
    return {"risk": "按本人已确认承受能力过滤", "fees": "按产品费用规则比较", "liquidity": "按期限与到账规则筛选"}[proposal["reason_key"]]


async def message(db: AsyncSession, user_id: str, bank: HttpWealthBankApi,
                  text: str, session_id: str | None, version: int | None,client_message_id: str | None=None) -> dict:
    """解释和方案预览入口，任何自然语言确认都不会执行交易。"""
    import os
    if client_message_id or os.getenv('WEALTH_NLU_MODE')=='model':
        from .wealth_dialogue import message as respond
        return await respond(db,user_id,bank,text,session_id,version,client_message_id or uuid4().hex)
    if re.search(r"验证码|TOTP|口令|密码|忽略.*(风控|授权|规则)|绕过.*验证|代答|替我.*(问卷|评估)|提高.*等级", text, re.I) or re.fullmatch(r"\d{6}", text.strip()):
        return {"mode": "offline", "kind": "clarification", "message": "请通过可信验证组件操作；理财对话不接收凭据或绕过安全规则。"}
    session = await db.get(WealthConversationORM, session_id) if session_id else None
    if session_id and (session is None or session.user_id != user_id):
        raise WealthPolicyError("会话不存在", "NOT_FOUND", 404)
    if session and version != session.version:
        raise WealthPolicyError("会话已更新，请刷新", "STALE_PLAN")
    sid = session.session_id if session else f"WC{uuid4().hex}"
    data = dict(session.data) if session else {"history": [], "pending": None}
    await db.rollback()
    response = {"mode": "offline", "kind": "clarification", "message": "离线有限表达：可查产品、对比产品ID、查持仓，或说明现金预算、期限和保留金额。交易须在结构化卡片确认与验证。"}
    if text.strip() in {"好的", "照办", "确认", "执行", "同意"}:
        response["message"] = "对话不会提交交易。请核对结构化订单逐笔确认，再通过验证器强验证。"
    elif "对比" in text:
        ids = list(dict.fromkeys(re.findall(r"M[WF]\d{3}", text.upper())))
        if 2 <= len(ids) <= 3:
            rows = [p for p in await bank.products(user_id) if p["product_id"] in ids]
            response = {"mode": "offline", "kind": "comparison", "products": rows,
                        "message": "客观对比请看风险、费用、持有期和赎回到账；净值日期不同不能直接比较收益。"}
    elif "持仓" in text or "资产" in text:
        response = {"mode": "offline", "kind": "assets", "assets": await bank.assets(user_id),
                    "message": "银行现金、占款、持仓与应收分开显示，外部模拟资产不可交易。"}
    elif "推荐" in text:
        try:
            suitability = await require_suitability(db, user_id, 1)
            await db.rollback()
            rows = candidates(await bank.products(user_id), suitability["capacity_level"], 365, [])[:3]
            response = {"mode": "offline", "kind": "products", "products": rows,
                "message": "按已确认承受能力、费用和流动性筛选最多3个虚构候选；默认365天比较窗口，近期用途请先说明期限。"}
        except WealthPolicyError as error:
            await db.rollback()
            response = {"mode": "offline", "kind": "assessment_required", "message": error.message, "code": error.code}
    elif re.search(r"买|申购", text) and re.search(r"M[WF]\d{3}", text.upper()):
        ids = re.findall(r"M[WF]\d{3}", text.upper())
        amount_match = re.search(r"(?:买|申购|金额)\s*([\d]+(?:\.\d{1,2})?[百千万]?|[一二两三四五六七八九十][百千万]?)", text)
        if len(ids) == 1 and amount_match:
            facts, products = await bank.assets(user_id), await bank.products(user_id)
            account = next((a for a in facts["cash"] if a["account_type"] != "credit"), None)
            product = next((p for p in products if p["product_id"] == ids[0]), None)
            if account and product:
                response = {"mode": "offline", "kind": "command", "message": "待确认申购意图，服务端预检仍检查本人评估、支付能力和产品规则。",
                    "command": {"operation_id": f"WO{uuid4().hex}", "direction": "purchase", "product_id": product["product_id"],
                        "product_version": product["version"], "disclosure_version": product["disclosure"]["version"],
                        "account_id": account["account_id"], "amount_fen": parse_money(amount_match[1]), "units": "0.0000"}}
    elif "产品" in text and not any(word in text for word in ("买", "申购", "配置")):
        response = {"mode": "offline", "kind": "products", "products": await bank.products(user_id),
                    "message": "以下均为虚构产品，净值历史含下跌，不保证本金或收益。"}
    elif "赎回" in text or "取出" in text:
        ids = re.findall(r"M[WF]\d{3}", text.upper())
        target = re.search(r"(?:取出|赎回金额)\s*(\d+(?:\.\d{1,2})?[百千万]?)", text)
        if len(ids) == 1 and ("一半" in text or "全部" in text or target):
            facts = await bank.assets(user_id)
            lots = [lot for lot in facts["lots"] if lot["product_id"] == ids[0]]
            accounts = {lot["account_id"] for lot in lots}
            total = sum((Decimal(lot["units"]) for lot in lots), Decimal(0))
            quantity = (total/2 if "一半" in text else total).quantize(Decimal("0.0001"))
            if target and lots and lots[0]["nav"]:
                quantity = (Decimal(parse_money(target[1]))/100/Decimal(lots[0]["nav"])).quantize(Decimal("0.0001"), ROUND_UP)
            if len(accounts) == 1 and quantity > 0:
                product = next(p for p in await bank.products(user_id) if p["product_id"] == ids[0])
                response = {"mode": "offline", "kind": "command", "message": "按当前净值估算具体份额，费用与未来净值使实际到账不同，不保证目标金额或自动补差；全部/一半按持有总额计算，锁定或份额不足由预检拒绝。",
                    "command": {"operation_id": f"WO{uuid4().hex}", "direction": "redeem", "product_id": ids[0],
                        "product_version": product["version"], "disclosure_version": product["disclosure"]["version"],
                        "account_id": next(iter(accounts)), "amount_fen": 0, "units": format(quantity, ".4f")}}
    else:
        money_pattern = r"([\d]+(?:\.\d{1,2})?[百千万]?|[一二两三四五六七八九十][百千万]?)"
        budget = re.search(r"(?:有|预算|投入)"+money_pattern, text)
        reserve = re.search(r"(?:留|保留)"+money_pattern, text)
        months = re.search(r"([一二两三四五六七八九十]|\d+)个月", text)
        facts = await bank.assets(user_id)
        account = next((a for a in facts["cash"] if a["account_type"] != "credit"), None)
        pending = dict(data.get("pending") or {})
        if budget and account:
            pending.update(kind="new_cash", account_id=account["account_id"], budget_fen=parse_money(budget[1]),
                           reserve_fen=0, horizon_days=365, excluded_products=[], version=1)
        if reserve and pending:
            pending["reserve_fen"] = parse_money(reserve[1])
        if months and pending:
            pending["horizon_days"] = parse_money(months[1])//100*30
        if pending:
            data["pending"] = pending
            try:
                await require_suitability(db, user_id, 1, allow_cash_only=True)
                await db.rollback()
                plan = await wealth_plan.create(db, user_id, bank, pending)
                response = {"mode": "offline", "kind": "plan", "plan": plan, "message": "已生成待确认模拟方案；请逐笔核对，不会自动交易。"}
                data["pending"] = None
            except WealthPolicyError as error:
                await db.rollback()
                response = {"mode": "offline", "kind": "assessment_required", "message": error.message,
                            "pending": pending, "code": error.code}
    row = await db.get(WealthConversationORM, sid)
    data["history"] = [*data["history"], {"user": text, "reply": response}][-20:]
    if row is None:
        row = WealthConversationORM(session_id=sid, user_id=user_id, version=1, data=data)
        db.add(row)
    else:
        row.version += 1
        row.data = data
    await db.commit()
    return {**response, "session_id": row.session_id, "version": row.version}
