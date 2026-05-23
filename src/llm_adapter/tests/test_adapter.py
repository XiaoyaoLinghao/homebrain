"""
Tests for LLM Adapter module.

Uses pytest + pytest-asyncio. All external dependencies (HABridgeClient,
LLM API) are mocked — no real network calls.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, "/root/coding/homebrain-v2/src")

from llm_adapter.ha_context import (
    HAContextBuilder,
    _state_text,
    _sanitize_fn_name,
    _entity_friendly_name,
    _DOMAIN_LABELS,
    _DOMAIN_SERVICES,
    _SERVICE_PARAMS,
)
from llm_adapter.adapter import LLMAdapter, MockLLMClient, DeepSeekClient, _SYSTEM_PROMPT_TEMPLATE


# ── test fixtures ────────────────────────────────────────────────────

@pytest.fixture
def mock_ha_client():
    """Return a mocked HABridgeClient with realistic HA data."""
    client = AsyncMock()
    client.get_all_states.return_value = [
        {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "客厅灯", "brightness": 255},
        },
        {
            "entity_id": "light.bedroom",
            "state": "off",
            "attributes": {"friendly_name": "卧室灯"},
        },
        {
            "entity_id": "sensor.temp_living",
            "state": "24.5",
            "attributes": {
                "friendly_name": "客厅温度",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
            },
        },
        {
            "entity_id": "climate.bedroom_ac",
            "state": "off",
            "attributes": {
                "friendly_name": "卧室空调",
                "temperature": 26,
                "current_temperature": 28,
                "hvac_mode": "off",
            },
        },
        {
            "entity_id": "cover.living_curtain",
            "state": "open",
            "attributes": {"friendly_name": "客厅窗帘", "current_position": 100},
        },
        {
            "entity_id": "fan.living_fan",
            "state": "off",
            "attributes": {"friendly_name": "客厅风扇", "percentage": 0},
        },
        {
            "entity_id": "lock.front_door",
            "state": "locked",
            "attributes": {"friendly_name": "前门锁"},
        },
        # Hidden entities should be filtered out
        {"entity_id": "update.home_assistant_core", "state": "on", "attributes": {}},
        {"entity_id": "sun.sun", "state": "above_horizon", "attributes": {}},
    ]
    client.get_entity_state.return_value = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {"friendly_name": "客厅灯", "brightness": 255},
    }
    client.call_service.return_value = [{"success": True}]
    client.get_services.return_value = None  # force fallback to built-in
    return client


@pytest.fixture
def context_builder(mock_ha_client):
    return HAContextBuilder(mock_ha_client)


@pytest.fixture
def mock_llm():
    return MockLLMClient()


# ── _state_text ──────────────────────────────────────────────────────

def test_state_text_light_on():
    entity = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {"friendly_name": "客厅灯", "brightness": 255},
    }
    result = _state_text(entity)
    assert "light.living_room" in result
    assert "客厅灯" in result
    assert "brightness=255" in result
    assert "灯光" in result


def test_state_text_light_off():
    entity = {
        "entity_id": "light.bedroom",
        "state": "off",
        "attributes": {"friendly_name": "卧室灯"},
    }
    result = _state_text(entity)
    assert "off" in result
    assert "brightness" not in result


def test_state_text_sensor():
    entity = {
        "entity_id": "sensor.temp_living",
        "state": "24.5",
        "attributes": {"friendly_name": "客厅温度", "unit_of_measurement": "°C"},
    }
    result = _state_text(entity)
    assert "24.5°C" in result
    assert "客厅温度" in result


def test_state_text_climate():
    entity = {
        "entity_id": "climate.bedroom_ac",
        "state": "off",
        "attributes": {
            "friendly_name": "卧室空调",
            "temperature": 26,
            "current_temperature": 28,
            "hvac_mode": "off",
        },
    }
    result = _state_text(entity)
    assert "off" in result
    assert "目标温度=26°C" in result
    assert "当前温度=28°C" in result


def test_state_text_cover():
    entity = {
        "entity_id": "cover.living_curtain",
        "state": "open",
        "attributes": {"friendly_name": "客厅窗帘", "current_position": 100},
    }
    result = _state_text(entity)
    assert "开度=100%" in result


def test_state_text_no_friendly_name():
    entity = {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {},
    }
    result = _state_text(entity)
    assert "kitchen" in result  # fallback to entity_id segment


# ── _sanitize_fn_name ────────────────────────────────────────────────

def test_sanitize_fn_name_normal():
    assert _sanitize_fn_name("ha_light_turn_on") == "ha_light_turn_on"


def test_sanitize_fn_name_with_special_chars():
    assert _sanitize_fn_name("ha light.turn-on") == "ha_light_turn_on"


# ── _entity_friendly_name ────────────────────────────────────────────

def test_entity_friendly_name():
    e = {"attributes": {"friendly_name": "客厅灯"}}
    assert _entity_friendly_name(e) == "客厅灯"


def test_entity_friendly_name_fallback():
    e = {"entity_id": "light.kitchen", "attributes": {}}
    assert _entity_friendly_name(e) == "kitchen"


# ── build_device_context ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_device_context_format(context_builder, mock_ha_client):
    ctx = await context_builder.build_device_context()
    assert "当前设备状态：" in ctx
    assert "light.living_room" in ctx
    assert "客厅灯" in ctx
    assert "sensor.temp_living" in ctx
    # Hidden entities should NOT appear
    assert "update.home_assistant_core" not in ctx
    assert "sun.sun" not in ctx


@pytest.mark.asyncio
async def test_build_device_context_empty(mock_ha_client):
    mock_ha_client.get_all_states.return_value = None
    builder = HAContextBuilder(mock_ha_client)
    ctx = await builder.build_device_context()
    assert "不可用" in ctx or "未连接" in ctx


@pytest.mark.asyncio
async def test_build_device_context_no_entities(mock_ha_client):
    mock_ha_client.get_all_states.return_value = []
    builder = HAContextBuilder(mock_ha_client)
    ctx = await builder.build_device_context()
    assert "没有可控制的设备" in ctx


# ── build_function_definitions ───────────────────────────────────────

@pytest.mark.asyncio
async def test_build_function_definitions_fallback(context_builder):
    """When get_services returns None, use built-in domain→service mapping."""
    defs = await context_builder.build_function_definitions()
    assert len(defs) > 0
    # Every definition must have the OpenAI structure
    for d in defs:
        assert d["type"] == "function"
        assert "name" in d["function"]
        assert "description" in d["function"]
        assert "parameters" in d["function"]
        # Function names follow ha_{domain}_{service} pattern
        assert d["function"]["name"].startswith("ha_")


@pytest.mark.asyncio
async def test_build_function_definitions_dynamic(mock_ha_client):
    """When get_services returns data, build from real catalog."""
    mock_ha_client.get_services.return_value = [
        {
            "domain": "light",
            "services": {
                "turn_on": {"description": "Turn on a light"},
                "turn_off": {"description": "Turn off a light"},
            },
        },
        {
            "domain": "switch",
            "services": {
                "turn_on": {"description": "Turn on a switch"},
            },
        },
    ]
    builder = HAContextBuilder(mock_ha_client)
    defs = await builder.build_function_definitions()
    assert len(defs) == 3
    names = {d["function"]["name"] for d in defs}
    assert "ha_light_turn_on" in names
    assert "ha_light_turn_off" in names
    assert "ha_switch_turn_on" in names


# ── execute_function_call ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_function_call_success(context_builder):
    result = await context_builder.execute_function_call(
        "ha_light_turn_on", {"entity_id": "light.living_room"}
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_execute_function_call_no_entity_id(context_builder):
    result = await context_builder.execute_function_call(
        "ha_light_turn_on", {}
    )
    assert result["success"] is False
    assert "entity_id" in result["error"]


@pytest.mark.asyncio
async def test_execute_function_call_invalid_name(context_builder):
    result = await context_builder.execute_function_call(
        "bad", {"entity_id": "x"}
    )
    assert result["success"] is False


@pytest.mark.asyncio
async def test_execute_function_call_with_extra_args(context_builder, mock_ha_client):
    result = await context_builder.execute_function_call(
        "ha_light_turn_on",
        {"entity_id": "light.living_room", "brightness": 128, "rgb_color": [255, 0, 0]},
    )
    assert result["success"] is True
    # Verify call_service received the extra args
    call_args = mock_ha_client.call_service.call_args
    # call_args is (domain, service, service_data)
    assert call_args[0][0] == "light"
    assert call_args[0][1] == "turn_on"
    assert call_args[0][2]["brightness"] == 128
    assert call_args[0][2]["rgb_color"] == [255, 0, 0]


@pytest.mark.asyncio
async def test_execute_function_call_ha_unreachable(mock_ha_client):
    mock_ha_client.call_service.return_value = None
    builder = HAContextBuilder(mock_ha_client)
    result = await builder.execute_function_call(
        "ha_light_turn_on", {"entity_id": "light.living_room"}
    )
    assert result["success"] is False
    assert "unreachable" in result["error"]


# ── LLMAdapter.process (end-to-end) ──────────────────────────────────

@pytest.mark.asyncio
async def test_adapter_process_text_only(context_builder, mock_ha_client):
    """When LLM returns only text, no tool calls needed."""
    mock_llm = MockLLMClient()  # no pre-configured fn calls
    adapter = LLMAdapter(mock_ha_client, llm_client=mock_llm)
    # Override context_builder to use the mock
    adapter.context_builder = context_builder

    result = await adapter.process("现在家里温度是多少？")
    assert result["reply"] is not None
    assert result["tool_calls"] is None
    assert result["call_results"] == []


@pytest.mark.asyncio
async def test_adapter_process_with_tool_calls(context_builder, mock_ha_client):
    """When LLM returns tool_calls, execute them and return results."""
    mock_llm = MockLLMClient(function_call_result=[
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "ha_light_turn_on",
                "arguments": json.dumps({"entity_id": "light.living_room"}),
            },
        },
    ])
    adapter = LLMAdapter(mock_ha_client, llm_client=mock_llm)
    adapter.context_builder = context_builder

    result = await adapter.process("打开客厅灯")
    assert result["tool_calls"] is not None
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "ha_light_turn_on"
    assert len(result["call_results"]) == 1
    assert result["call_results"][0]["success"] is True
    # Verify call_service was called
    mock_ha_client.call_service.assert_called_once()


@pytest.mark.asyncio
async def test_adapter_process_multiple_tool_calls(context_builder, mock_ha_client):
    """LLM requests multiple function calls (e.g. 'turn off all lights')."""
    mock_llm = MockLLMClient(function_call_result=[
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "ha_light_turn_off",
                "arguments": json.dumps({"entity_id": "light.living_room"}),
            },
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {
                "name": "ha_light_turn_off",
                "arguments": json.dumps({"entity_id": "light.bedroom"}),
            },
        },
    ])
    adapter = LLMAdapter(mock_ha_client, llm_client=mock_llm)
    adapter.context_builder = context_builder

    result = await adapter.process("关闭所有灯")
    assert len(result["tool_calls"]) == 2
    assert len(result["call_results"]) == 2
    assert result["call_results"][0]["success"] is True
    assert result["call_results"][1]["success"] is True
    assert mock_ha_client.call_service.call_count == 2


@pytest.mark.asyncio
async def test_adapter_process_empty_message(mock_ha_client):
    mock_llm = MockLLMClient()
    adapter = LLMAdapter(mock_ha_client, llm_client=mock_llm)
    result = await adapter.process("")
    assert "帮助" in result["reply"] or "设备" in result["reply"]


@pytest.mark.asyncio
async def test_adapter_process_llm_error(mock_ha_client):
    """When LLM call raises, return friendly error."""
    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = Exception("API timeout")
    adapter = LLMAdapter(mock_ha_client, llm_client=mock_llm)
    adapter.context_builder = HAContextBuilder(mock_ha_client)

    result = await adapter.process("打开灯")
    assert "不可用" in result["reply"] or "重试" in result["reply"]


# ── DeepSeekClient initialization ──────────────────────────────────

def test_deepseek_client_defaults():
    client = DeepSeekClient()
    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-chat"


def test_deepseek_client_custom():
    client = DeepSeekClient(
        api_key="sk-test",
        base_url="https://custom.api",
        model="deepseek-v3",
    )
    assert client.api_key == "sk-test"
    assert client.base_url == "https://custom.api"
    assert client.model == "deepseek-v3"


# ── System prompt template ───────────────────────────────────────────

def test_system_prompt_contains_device_context():
    ctx = "test device context"
    prompt = _SYSTEM_PROMPT_TEMPLATE.format(device_context=ctx)
    assert ctx in prompt
    assert "HomeBrain" in prompt
    assert "设备上下文" in prompt


# ── DOMAIN_LABELS coverage ───────────────────────────────────────────

def test_domain_labels_covers_all_service_domains():
    """Every domain in _DOMAIN_SERVICES must have a label in _DOMAIN_LABELS."""
    for domain in _DOMAIN_SERVICES:
        assert domain in _DOMAIN_LABELS, f"Missing label for domain: {domain}"
