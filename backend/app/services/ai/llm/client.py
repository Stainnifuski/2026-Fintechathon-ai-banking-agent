"""所有文本生成和结构化提取共用的异步模型客户端。"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import LLMSettings, get_mode, get_settings
from .errors import LLMError

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=BaseModel)


def _validate_messages(messages: list[dict[str, Any]]) -> None:
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant", "developer")
                   or not isinstance(m.get("content"), str) for m in messages)):
        raise LLMError("MODEL_REQUEST_INVALID")


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    data: T
    evidence: dict[str, Any]


class LLMService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """transport 支持离线协议测试，不影响业务调用方式。"""
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._pool_config: tuple[float, float, int] | None = None
        self._slots: asyncio.Semaphore | None = None
        self._active = 0
        self._idle: asyncio.Event | None = None
        self._closing = False
        self._close_done: asyncio.Event | None = None

    async def aclose(self) -> None:
        """等待已接入请求结束后释放连接；关闭期间拒绝新请求。"""
        if self._closing:
            assert self._close_done is not None
            await self._close_done.wait()
            if self._client is not None:
                await self.aclose()
            return
        self._closing = True
        self._close_done = asyncio.Event()
        try:
            if self._idle is not None:
                await self._idle.wait()
            if self._client is not None:
                await self._client.aclose()
            self._client = None
            self._pool_config = None
            self._slots = None
            self._idle = None
        finally:
            self._closing = False
            self._close_done.set()

    async def _connection(self, settings: LLMSettings) -> httpx.AsyncClient:
        pool_config = (settings.timeout, settings.connect_timeout, settings.max_connections)
        if self._closing:
            raise LLMError("MODEL_CLOSING")
        if self._client is not None and not self._client.is_closed and self._pool_config != pool_config:
            # 运行期不替换池，避免关闭其他请求正在使用的连接。
            raise LLMError("MODEL_CONFIGURATION_CHANGED")
        if self._client is None or self._client.is_closed:
            try:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(settings.timeout, connect=settings.connect_timeout),
                    limits=httpx.Limits(max_connections=settings.max_connections,
                                       max_keepalive_connections=settings.max_connections),
                    transport=self._transport,
                )
            except (ValueError, httpx.InvalidURL):
                raise LLMError("MODEL_CONFIGURATION_INVALID") from None
            self._slots = asyncio.Semaphore(settings.max_connections)
            self._pool_config = pool_config
            self._idle = asyncio.Event()
            self._idle.set()
        return self._client

    async def _generate(
        self, messages: list[dict[str, Any]], *, structured: bool,
        max_tokens: int | None, temperature: float | None,
    ) -> LLMResult[str]:
        if get_mode() == "offline":
            raise LLMError("MODEL_DISABLED")
        settings = get_settings()
        _validate_messages(messages)
        if (max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1)
                or temperature is not None and (type(temperature) not in {int, float}
                                                or not 0 <= temperature <= 2)):
            raise LLMError("MODEL_REQUEST_INVALID")
        body: dict[str, Any] = {
            "model": settings.model, "messages": messages,
            "temperature": settings.temperature if temperature is None else temperature,
            "max_tokens": settings.max_tokens if max_tokens is None else min(max_tokens, settings.max_tokens),
        }
        if structured:
            body["response_format"] = {"type": "json_object"}
        if settings.provider == "deepseek":
            body["thinking"] = {"type": "disabled"}
        client = await self._connection(settings)
        slots, idle = self._slots, self._idle
        assert slots is not None and idle is not None
        self._active += 1
        idle.clear()
        try:
            # 总超时包括并发排队和网络等待；不自动重试请求。
            async with asyncio.timeout(settings.timeout):
                async with slots:
                    response = await client.post(
                        settings.base_url + "/chat/completions", json=body,
                        headers={"Authorization": "Bearer " + settings.api_key},
                    )
            if response.status_code != 200:
                raise LLMError("MODEL_HTTP_" + str(response.status_code))
            payload = response.json()
            choice = payload["choices"][0]
            if choice.get("finish_reason") not in {None, "stop"}:
                raise LLMError("MODEL_OUTPUT_INCOMPLETE")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMError("MODEL_RESPONSE_INVALID")
            # 仅保留数值 token 统计，避免服务商扩展字段泄露输入或凭据。
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                usage = {}
            usage = {key: value for key, value in usage.items()
                     if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                     and type(value) is int and value >= 0}
            return LLMResult(content, {"provider": settings.provider, "model": settings.model,
                                       "usage": usage, "live": True})
        except LLMError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise LLMError("MODEL_TIMEOUT") from None
        except httpx.HTTPError:
            raise LLMError("MODEL_NETWORK_ERROR") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("MODEL_RESPONSE_INVALID") from None
        finally:
            self._active -= 1
            if self._active == 0:
                idle.set()

    async def generate_text(
        self, *, messages: list[dict[str, Any]], max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult[str]:
        """生成文本并返回服务商、模型及 token 使用记录。"""
        return await self._generate(messages, structured=False, max_tokens=max_tokens, temperature=temperature)

    async def generate_structured(
        self, *, messages: list[dict[str, Any]], schema: type[ModelT],
        max_tokens: int | None = None, temperature: float | None = 0,
    ) -> LLMResult[ModelT]:
        """JSON 输出必须通过调用方提供的 Pydantic 结构校验。"""
        _validate_messages(messages)
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise LLMError("MODEL_REQUEST_INVALID")
        schema_message = {"role": "system", "content": "Return one JSON object matching this schema: "
                          + json.dumps(schema.model_json_schema(), ensure_ascii=False)}
        result = await self._generate([schema_message, *messages], structured=True,
                                      max_tokens=max_tokens, temperature=temperature)
        try:
            data = schema.model_validate_json(result.data)
        except ValidationError:
            raise LLMError("MODEL_SCHEMA_INVALID") from None
        return LLMResult(data, result.evidence)


llm = LLMService()
