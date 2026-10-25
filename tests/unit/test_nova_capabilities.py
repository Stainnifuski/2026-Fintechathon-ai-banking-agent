"""The capability directory is read-only and reflects registered NOVA tools."""
import json
from types import SimpleNamespace

import pytest

from backend.app.services.ai import nova
from backend.app.services.ai.capability_catalog import describe_capabilities


@pytest.mark.asyncio
async def test_capability_tool_lists_current_businesses_without_identity():
    result = await nova.execute_tool({
        "function": {"name": "describe_bank_capabilities", "arguments": "{}"},
    })
    assert result["data_kind"] == "service_catalog"
    domains = {item["domain"] for item in result["businesses"]}
    assert {"accounts", "bills", "planning", "contacts", "transfer", "collection",
            "cards", "subscriptions", "calendar", "shops", "wealth", "time"} <= domains
    for business in result["businesses"]:
        for feature in [*business["queries"], *business["operations"]]:
            assert feature["tool"] in nova.TOOL_REGISTRY
    transfer = next(item for item in result["businesses"] if item["domain"] == "transfer")
    collection = next(item for item in result["businesses"] if item["domain"] == "collection")
    assert "向别人付款" in transfer["operations"][0]["description"]
    assert "请求别人向本人付款" in collection["operations"][0]["description"]
    assert all("completion" not in feature for item in result["businesses"]
               for feature in [*item["queries"], *item["operations"]])


def test_capability_catalog_hides_unregistered_features():
    result = describe_capabilities({"get_current_time", "manage_transfer_task"})
    assert {item["domain"] for item in result["businesses"]} == {"time", "transfer"}
    transfer = next(item for item in result["businesses"] if item["domain"] == "transfer")
    assert transfer["queries"] == []
    assert [item["tool"] for item in transfer["operations"]] == ["manage_transfer_task"]


@pytest.mark.asyncio
async def test_capability_route_uses_only_directory_tool(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "model")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    seen = []

    async def structured(**kwargs):
        assert kwargs["schema"] is nova.RequestRoute
        return SimpleNamespace(data=nova.RequestRoute(
            mode="direct_react", domains=["capabilities"], has_dependencies=False))

    async def with_tools(**kwargs):
        seen.append(kwargs)
        assert kwargs["tool_choice"] == "required"
        assert [tool["function"]["name"] for tool in kwargs["tools"]] == [
            "describe_bank_capabilities"]
        return SimpleNamespace(data={"role": "assistant", "content": None,
            "tool_calls": [{"id": "capabilities-1", "type": "function",
                            "function": {"name": "describe_bank_capabilities",
                                         "arguments": json.dumps({})}}]})

    monkeypatch.setattr(nova.llm, "generate_structured", structured)
    monkeypatch.setattr(nova.llm, "generate_with_tools", with_tools)
    result = await nova.chat("你能做哪些银行业务？", [])
    assert len(seen) == 1
    assert [call["name"] for call in result["tool_calls"]] == ["describe_bank_capabilities"]
    assert result["grounding"]["status"] == "grounded"
    assert "账户与资产" in result["reply"]
    assert "收款" in result["reply"]
    assert "本人确认" not in result["reply"]
