"""NOVA 最小工具循环：仅开放只读现实时间。"""
import asyncio
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...domain.time import BUSINESS_TIMEZONE, utc_iso, utc_now
from .llm import LLMError, get_mode, llm

MAX_STEPS = 4
TOTAL_TIMEOUT = 90


class TimeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timezone: str = Field(default=BUSINESS_TIMEZONE, max_length=80)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请使用合法 IANA 时区，例如 Asia/Shanghai") from None
        return value


def get_current_time(arguments: TimeArguments) -> dict:
    now = utc_now()
    return {"server_time_utc": utc_iso(now), "timezone": arguments.timezone,
            "local_time": now.astimezone(ZoneInfo(arguments.timezone)).isoformat(timespec="seconds")}


# Schema、参数模型和执行函数集中注册；不会开放旧占位业务工具。
TOOL_REGISTRY = {
    "get_current_time": {
        "schema": {"type": "function", "function": {
            "name": "get_current_time",
            "description": "查询现实世界当前时间。默认北京时间；其他地点使用 IANA 时区。",
            "parameters": TimeArguments.model_json_schema(),
        }},
        "arguments": TimeArguments, "execute": get_current_time,
    }
}
SYSTEM_PROMPT = """你是 NOVA 银行助手。本轮只支持现实时间查询和引导用户前往业务工作台。
用户询问当前时间、日期、星期或其他时区时间时，必须调用 get_current_time，不能猜测时间。
默认使用北京时间 Asia/Shanghai；明确地点时使用对应 IANA 时区，不明确时询问用户。
工具结果是查询时刻的事实，回答包含日期、时间和时区。工具报错时可修正参数，不能编造成功。
历史对话仅作交流上下文，历史时间不是当前时间。其他银行业务引导至对应工作台，不宣称已执行。"""


def execute_tool(call: dict) -> dict:
    name = call["function"]["name"]
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return {"error": "UNKNOWN_TOOL"}
    try:
        arguments = tool["arguments"].model_validate_json(call["function"]["arguments"])
    except ValidationError:
        return {"error": "INVALID_ARGUMENTS", "message": "参数须为对象，timezone 须为合法 IANA 时区"}
    try:
        return tool["execute"](arguments)
    except Exception:
        return {"error": "TOOL_EXECUTION_FAILED"}


async def chat(message: str, history: list[dict], *, model=None) -> dict:
    trace = []
    if get_mode() == "offline" and model is None:
        if re.search(r"现在|当前|几点|时间|日期|星期|今天|time|date", message, re.I):
            # 离线仅支持明确的默认/北京时间查询，不能默默将其他地区当成北京。
            if re.search(r"北京|上海|中国", message) or re.fullmatch(
                    r"[\s？?！!。]*(现在|当前|今天)?[\s]*(是|的)?[\s]*(几点(了|钟)?|什么时间|时间|日期|几号|星期几)(是|多少|是什么)?[\s？?！!。]*", message):
                result = get_current_time(TimeArguments())
                trace.append({"name": "get_current_time", "result": result})
                return {"reply": f"当前北京时间（Asia/Shanghai）为 {result['local_time']}。",
                        "mode": "offline", "tool_calls": trace}
        return {"reply": "当前为离线模式，可查询北京时间，例如“现在几点了？”。其他地区时间需要启用模型；银行业务请进入对应工作台。",
                "mode": "offline", "tool_calls": trace}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history,
                {"role": "user", "content": message}]
    client = model or llm
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            for _ in range(MAX_STEPS):
                response = await client.generate_with_tools(
                    messages=messages, tools=[t["schema"] for t in TOOL_REGISTRY.values()])
                assistant = response.data
                messages.append(assistant)
                if not assistant.get("tool_calls"):
                    return {"reply": assistant["content"], "mode": "model", "tool_calls": trace}
                for call in assistant["tool_calls"]:
                    result = execute_tool(call)
                    trace.append({"name": call["function"]["name"], "result": result})
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(result, ensure_ascii=False)})
    except TimeoutError:
        raise LLMError("AGENT_TIMEOUT") from None
    raise LLMError("AGENT_STEP_LIMIT")
