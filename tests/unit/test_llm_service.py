"""统一配置、真实 HTTP 协议替身及跨业务接入回归；不调用外网。"""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict

from backend.app.services.ai import bills, nlu, wealth_model
from backend.app.services.ai.llm import LLMError, LLMService, get_mode, get_settings


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str


MESSAGES = [{"role": "user", "content": "虚构测试问题"}]


def response(content, **kwargs):
    return httpx.Response(200, json={"choices": [{"message": {"content": content},
                                                 "finish_reason": "stop"}], **kwargs})


@pytest_asyncio.fixture
async def service(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    monkeypatch.setenv("LLM_MODE", "auto")
    seen = []

    def handler(request):
        seen.append(request)
        return response('{"answer":"测试回答"}', usage={"total_tokens": 12, "secret": "discard"})

    client = LLMService(httpx.MockTransport(handler))
    yield client, seen
    await client.aclose()


def test_only_one_key_enables_every_module(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "auto")
    assert get_mode() == "offline"
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    # 旧模板中的业务 offline 开关不能阻止全局 auto。
    for name in ("TRANSFER_NLU_MODE", "BILL_NLU_MODE", "WEALTH_NLU_MODE"):
        monkeypatch.setenv(name, "offline")
    settings = get_settings()
    assert settings.mode == get_mode() == nlu.NLUEngine().mode == "model"
    assert settings.base_url == "https://api.deepseek.com"
    assert settings.model == "deepseek-flash"
    assert "test-secret" not in repr(settings)
    monkeypatch.setenv("LLM_MODE", "offline")
    assert get_mode() == nlu.NLUEngine().mode == "offline"


@pytest.mark.parametrize("key", ["", "  ", "your-api-key", "your-api-key-here", "placeholder", "替换为你的密钥"])
def test_placeholder_key_keeps_auto_offline(monkeypatch, key):
    monkeypatch.setenv("LLM_MODE", "auto")
    monkeypatch.setenv("LLM_API_KEY", key)
    assert get_mode() == "offline"


def test_global_bundle_wins_and_legacy_bundle_is_atomic(monkeypatch, caplog):
    monkeypatch.setenv("LLM_MODE", "auto")
    monkeypatch.setenv("LLM_API_KEY", "canonical-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://canonical.example/v1/")
    monkeypatch.setenv("LLM_MODEL", "canonical-model")
    monkeypatch.setenv("TRANSFER_MODEL_API_KEY", "legacy-secret")
    monkeypatch.setenv("TRANSFER_MODEL_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("TRANSFER_MODEL_NAME", "legacy-model")
    monkeypatch.setenv("WEALTH_MODEL_NAME", "wrong-model")
    settings = get_settings()
    assert (settings.api_key, settings.base_url, settings.model) == (
        "canonical-secret", "https://canonical.example/v1", "canonical-model")
    monkeypatch.delenv("LLM_API_KEY")
    settings = get_settings()
    assert (settings.api_key, settings.base_url, settings.model) == (
        "legacy-secret", "https://legacy.example/v1", "legacy-model")
    assert "secret" not in caplog.text


@pytest.mark.parametrize("name,value", [
    ("LLM_MODE", "typo"), ("LLM_PROVIDER", "typo"),
    ("LLM_TIMEOUT", "NaN"), ("LLM_TIMEOUT", "0"),
    ("LLM_CONNECT_TIMEOUT", "-1"), ("LLM_MAX_CONNECTIONS", "1.5"),
    ("LLM_MAX_TOKENS", "0"), ("LLM_TEMPERATURE", "3"),
    ("LLM_BASE_URL", "https://secret:password@example.com/v1"),
    ("LLM_BASE_URL", "https://example.com/v1?key=secret"),
    ("LLM_BASE_URL", "not-a-url"),
    ("LLM_BASE_URL", "https://[broken"),
    ("LLM_BASE_URL", "https://example.com:invalid/v1"),
    ("LLM_BASE_URL", "https://example.com/v1/chat/completions"),
    ("LLM_API_KEY", "secret\ninvalid"),
])
def test_invalid_settings_have_safe_error(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(LLMError, match="^MODEL_CONFIGURATION_INVALID$"):
        get_settings()


def test_custom_service_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://compatible.example/v1")
    with pytest.raises(LLMError, match="MODEL_CONFIGURATION_MISSING"):
        get_settings()


def test_legacy_key_inherits_complete_shared_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "auto")
    monkeypatch.setenv("TRANSFER_MODEL_API_KEY", "legacy-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://compatible.example/v1")
    monkeypatch.setenv("LLM_MODEL", "shared-model")
    settings = get_settings()
    assert (settings.base_url, settings.model) == ("https://compatible.example/v1", "shared-model")
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.delenv("LLM_MODEL")
    with pytest.raises(LLMError, match="MODEL_CONFIGURATION_MISSING"):
        get_settings()


@pytest.mark.asyncio
async def test_text_structured_pool_reuse_and_provider_options(service, monkeypatch):
    client, seen = service
    text = await client.generate_text(messages=MESSAGES)
    pooled = client._client
    structured = await client.generate_structured(messages=MESSAGES, schema=Output)
    assert client._client is pooled and len(seen) == 2
    assert text.data == '{"answer":"测试回答"}'
    assert structured.data.answer == "测试回答"
    assert structured.evidence == {"provider": "deepseek", "model": "deepseek-flash",
                                   "usage": {"total_tokens": 12}, "live": True}
    text_body, structured_body = [json.loads(r.content) for r in seen]
    assert "response_format" not in text_body
    assert structured_body["response_format"] == {"type": "json_object"}
    assert structured_body["thinking"] == {"type": "disabled"}
    assert "test-secret" not in str(structured_body)
    monkeypatch.setenv("LLM_BASE_URL", "https://compatible.example/v1/")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    await client.generate_structured(messages=MESSAGES, schema=Output)
    assert str(seen[-1].url) == "https://compatible.example/v1/chat/completions"
    assert seen[-1].headers["Authorization"] == "Bearer test-secret"
    assert "thinking" not in json.loads(seen[-1].content)
    await client.aclose()
    assert pooled.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [("offline", "MODEL_DISABLED"), ("model", "MODEL_CONFIGURATION_MISSING")])
async def test_disabled_or_missing_key_never_sends(service, monkeypatch, mode, code):
    client, seen = service
    monkeypatch.setenv("LLM_MODE", mode)
    monkeypatch.delenv("LLM_API_KEY")
    with pytest.raises(LLMError, match=code):
        await client.generate_text(messages=MESSAGES)
    assert not seen


@pytest.mark.asyncio
async def test_offline_does_not_validate_unused_provider(service, monkeypatch):
    client, seen = service
    monkeypatch.setenv("LLM_MODE", "offline")
    monkeypatch.setenv("LLM_BASE_URL", "https://[broken")
    with pytest.raises(LLMError, match="^MODEL_DISABLED$"):
        await client.generate_text(messages=MESSAGES)
    assert not seen


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"max_tokens": 0}, {"max_tokens": -1}, {"max_tokens": True}, {"max_tokens": 1.5},
    {"temperature": float("nan")}, {"temperature": 3}, {"temperature": "secret"},
])
async def test_invalid_request_options_never_send(service, options):
    client, seen = service
    with pytest.raises(LLMError, match="^MODEL_REQUEST_INVALID$"):
        await client.generate_text(messages=MESSAGES, **options)
    assert not seen


@pytest.mark.asyncio
async def test_optional_usage_metadata_cannot_break_valid_answer(service):
    client, _ = service
    client._transport = httpx.MockTransport(lambda _: response("有效文本", usage=["invalid metadata"]))
    result = await client.generate_text(messages=MESSAGES)
    assert result.data == "有效文本" and result.evidence["usage"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("messages", [None, [], [{"role": "user"}], [{"role": "unsupported", "content": "test"}]])
async def test_invalid_messages_fail_before_network_for_both_methods(service, messages):
    client, seen = service
    with pytest.raises(LLMError, match="^MODEL_REQUEST_INVALID$"):
        await client.generate_text(messages=messages)
    with pytest.raises(LLMError, match="^MODEL_REQUEST_INVALID$"):
        await client.generate_structured(messages=messages, schema=Output)
    assert not seen


@pytest.mark.asyncio
async def test_invalid_schema_and_client_initialization_are_sanitized(service, monkeypatch):
    client, seen = service
    with pytest.raises(LLMError, match="^MODEL_REQUEST_INVALID$"):
        await client.generate_structured(messages=MESSAGES, schema=dict)

    def bad_client(**kwargs):
        raise ValueError("private proxy credentials")

    monkeypatch.setattr(httpx, "AsyncClient", bad_client)
    with pytest.raises(LLMError, match="^MODEL_CONFIGURATION_INVALID$"):
        await client.generate_text(messages=MESSAGES)
    assert not seen


@pytest.mark.asyncio
async def test_request_cancellation_unblocks_concurrent_closers(service):
    client, _ = service
    entered = asyncio.Event()

    async def blocked(_):
        entered.set()
        await asyncio.Event().wait()

    client._transport = httpx.MockTransport(blocked)
    running = asyncio.create_task(client.generate_text(messages=MESSAGES))
    await entered.wait()
    pool = client._client
    closers = [asyncio.create_task(client.aclose()) for _ in range(2)]
    await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.wait_for(asyncio.gather(*closers), timeout=0.1)
    assert pool.is_closed and client._active == 0


@pytest.mark.asyncio
async def test_cancelled_close_can_be_retried_without_losing_pool(service):
    client, _ = service
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(_):
        entered.set()
        await release.wait()
        return response("有效文本")

    client._transport = httpx.MockTransport(blocked)
    running = asyncio.create_task(client.generate_text(messages=MESSAGES))
    await entered.wait()
    pool = client._client
    closing = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert client._client is pool and not client._closing
    release.set()
    assert (await running).data == "有效文本"
    await client.aclose()
    assert pool.is_closed


@pytest.mark.asyncio
async def test_pool_change_and_close_preserve_in_flight_request(service, monkeypatch):
    client, _ = service
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(_):
        entered.set()
        await release.wait()
        return response("有效文本")

    client._transport = httpx.MockTransport(handler)
    running = asyncio.create_task(client.generate_text(messages=MESSAGES))
    await entered.wait()
    pool = client._client
    monkeypatch.setenv("LLM_MAX_CONNECTIONS", "2")
    try:
        with pytest.raises(LLMError, match="^MODEL_CONFIGURATION_CHANGED$"):
            await asyncio.wait_for(client.generate_text(messages=MESSAGES), timeout=0.1)
        assert not pool.is_closed
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        assert not closing.done() and not pool.is_closed
        with pytest.raises(LLMError, match="^MODEL_CLOSING$"):
            await client.generate_text(messages=MESSAGES)
    finally:
        release.set()
        result = await running
    await closing
    assert result.data == "有效文本" and pool.is_closed
    assert client._client is None


@pytest.mark.asyncio
async def test_injected_nlu_does_not_require_provider_configuration(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "offline")
    monkeypatch.setenv("LLM_BASE_URL", "https://[broken")

    class RecordedModel:
        async def extract(self, *args):
            return {"intent": "transfer", "recipient": "李四", "amount_text": "300元"}

    result = await nlu.NLUEngine(RecordedModel()).extract("给李四转300元", {})
    assert result.recipient == "李四"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "initialization", "request", "worker", "llm_close"])
async def test_lifecycle_always_releases_llm_and_database(monkeypatch, failure, caplog):
    from backend.app import main
    events = []

    async def init_db():
        if failure == "initialization":
            raise RuntimeError("initialization failure")

    async def no_op():
        pass

    async def close_db():
        events.append("database")

    async def close_llm():
        events.append("llm")
        if failure == "llm_close":
            raise RuntimeError("close failure")

    async def worker(*args):
        try:
            if failure == "worker":
                raise RuntimeError("worker failure")
            await asyncio.Event().wait()
        finally:
            events.append("worker")

    monkeypatch.setenv("LLM_MODE", "offline")
    monkeypatch.setenv("LLM_BASE_URL", "https://[broken")
    monkeypatch.setattr(main, "init_db", init_db)
    monkeypatch.setattr(main, "seed_data", no_op)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "get_bank_api", lambda: object())
    monkeypatch.setattr(main.llm, "aclose", close_llm)
    monkeypatch.setenv('WEALTH_CATALOG_AUTO_UPGRADE', 'false')
    from contextlib import asynccontextmanager
    from backend.app.services.bank import wealth_retire_pending, bill_foundation
    async def no_bank_work(*args, **kwargs):
        return {}
    class Session:
        async def commit(self): pass
    @asynccontextmanager
    async def sessions():
        yield Session()
    monkeypatch.setattr(main, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(wealth_retire_pending, 'retire', no_bank_work)
    monkeypatch.setattr(bill_foundation, 'refresh_all', no_bank_work)
    monkeypatch.setattr(main.app.state.wealth_portfolio_source, 'preload', no_op)
    for name in ("scheduled_transfer_loop", "subscription_charge_loop", "wealth_task_loop"):
        monkeypatch.setattr(main, name, worker)

    async def run():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            if failure == "request":
                raise RuntimeError("request failure")

    if failure in {"initialization", "request", "llm_close"}:
        with pytest.raises(RuntimeError):
            await run()
    else:
        await run()
    assert events[-2:] == ["llm", "database"]
    assert events.count("worker") == (0 if failure == "initialization" else 3)
    if failure == "worker":
        assert "后台任务" in caplog.text and "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_forced_model_missing_key_fails_before_database_start(monkeypatch):
    from backend.app import main

    async def must_not_start():
        raise AssertionError("must reject configuration before initializing database")

    async def no_op():
        pass

    monkeypatch.setenv("LLM_MODE", "model")
    monkeypatch.setattr(main, "init_db", must_not_start)
    monkeypatch.setattr(main, "close_db", no_op)
    with pytest.raises(LLMError, match="MODEL_CONFIGURATION_MISSING"):
        async with main.lifespan(main.app):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ({"choices": []}, "MODEL_RESPONSE_INVALID"),
    ({"choices": [{"message": {"content": None}}]}, "MODEL_RESPONSE_INVALID"),
    ({"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}, "MODEL_OUTPUT_INCOMPLETE"),
    ({"choices": [{"message": {"content": "not json"}}]}, "MODEL_SCHEMA_INVALID"),
    ({"choices": [{"message": {"content": '{"answer":4}'}}]}, "MODEL_SCHEMA_INVALID"),
    ({"choices": [{"message": {"content": '{"answer":"ok","execute":true}'}}]}, "MODEL_SCHEMA_INVALID"),
])
async def test_untrusted_model_response_is_rejected(service, payload, code):
    client, _ = service
    client._transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    with pytest.raises(LLMError, match=code):
        await client.generate_structured(messages=MESSAGES, schema=Output)


@pytest.mark.asyncio
async def test_http_network_timeout_are_sanitized_without_retries(service):
    client, _ = service
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "test-secret and private input"})

    client._transport = httpx.MockTransport(fail)
    with pytest.raises(LLMError, match="^MODEL_HTTP_429$") as error:
        await client.generate_text(messages=MESSAGES)
    assert len(calls) == 1 and "test-secret" not in str(error.value)
    assert error.value.__cause__ is None
    await client.aclose()

    def network_failure(request):
        raise httpx.ConnectError("test-secret", request=request)

    client._transport = httpx.MockTransport(network_failure)
    with pytest.raises(LLMError, match="^MODEL_NETWORK_ERROR$"):
        await client.generate_text(messages=MESSAGES)
    await client.aclose()

    def timeout(request):
        raise httpx.ReadTimeout("test-secret", request=request)

    client._transport = httpx.MockTransport(timeout)
    with pytest.raises(LLMError, match="^MODEL_TIMEOUT$"):
        await client.generate_text(messages=MESSAGES)


@pytest.mark.asyncio
async def test_concurrency_queue_and_network_share_total_deadline(service, monkeypatch):
    client, _ = service
    monkeypatch.setenv("LLM_TIMEOUT", "0.03")
    monkeypatch.setenv("LLM_MAX_CONNECTIONS", "1")
    active = peak = calls = 0

    async def slow(request):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(1)
            return response("unused")
        finally:
            active -= 1

    client._transport = httpx.MockTransport(slow)
    await client._connection(get_settings())
    await client._slots.acquire()
    try:
        queued = await asyncio.gather(*(client.generate_text(messages=MESSAGES) for _ in range(3)),
                                      return_exceptions=True)
    finally:
        client._slots.release()
    assert all(isinstance(r, LLMError) and r.code == "MODEL_TIMEOUT" for r in queued)
    assert calls == 0
    results = await asyncio.gather(*(client.generate_text(messages=MESSAGES) for _ in range(3)),
                                   return_exceptions=True)
    assert all(isinstance(r, LLMError) and r.code == "MODEL_TIMEOUT" for r in results)
    assert peak == 1 and 1 <= calls <= 3 and active == 0


@pytest.mark.asyncio
async def test_all_business_modules_use_one_key_endpoint_and_client(service, monkeypatch):
    client, seen = service
    monkeypatch.setattr(nlu, "llm", client)
    monkeypatch.setattr(bills, "llm", client)
    monkeypatch.setattr(wealth_model, "llm", client)
    outputs = [
        {"intent": "transfer", "recipient": "李四", "amount_text": "300元"},
        {"intent": "bill", "category_text": "餐饮"},
        {"intent": "assets", "semantic": {"patches": []}},
        {"answer": "资料说明了规则。", "citation_ids": ["D1"]},
    ]

    def handler(request):
        seen.append(request)
        return response(json.dumps(outputs.pop(0), ensure_ascii=False))

    client._transport = httpx.MockTransport(handler)
    transfer = await nlu.NLUEngine().extract("给李四转300元", {"missing_recipient": False, "secret": "discard"})
    pooled = client._client
    bill, mode = await bills.understand("查看餐饮消费", bills.BillQuery(period="month", date="2026-09"))
    wealth, evidence = await wealth_model.understand("查询我的资产", {})
    answer, citations, _ = await wealth_model.explain("解释规则", [
        {"document_id": "D1", "title": "规则", "excerpt": "资料说明了规则。"}])
    assert transfer.recipient == "李四"
    assert bill.category == "dining" and mode == "model"
    assert wealth.intent == "assets" and evidence["live"]
    assert '资料摘录' in answer and '资料说明了规则。' in answer and citations[0]["document_id"] == "D1"
    assert client._client is pooled and len(seen) == 4
    assert len({str(r.url) for r in seen}) == 1
    assert all(r.headers["Authorization"] == "Bearer test-secret" for r in seen)
    assert all(json.loads(r.content)["model"] == "deepseek-flash" for r in seen)
    assert "discard" not in seen[0].content.decode()


def test_root_dotenv_loading_preserves_process_overrides(tmp_path, monkeypatch):
    from backend.app import environment
    root = tmp_path / "source"
    app = root / "backend" / "app"
    app.mkdir(parents=True)
    (root / ".env").write_text("LLM_API_KEY=test-file-key\nLLM_MODEL=file-model\n")
    monkeypatch.setattr(environment, "__file__", str(app / "environment.py"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("LLM_MODEL", "process-model")
    environment.load_environment()
    assert get_settings().api_key == "test-file-key"
    assert get_settings().model == "process-model"
    # Docker /app/app 布局仍以进程 env_file 注入的环境为准。
    docker = tmp_path / "app" / "app"
    docker.mkdir(parents=True)
    monkeypatch.setattr(environment, "__file__", str(docker / "environment.py"))
    environment.load_environment()
    assert get_settings().model == "process-model"
