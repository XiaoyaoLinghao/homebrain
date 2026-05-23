"""
Tests for scene_engine.engine and scene_engine.ha_transport.

Uses pytest + asyncio. Mocks HABridgeClient to avoid real HA connectivity.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure base URL & token available for constructors
os.environ["HA_API_URL"] = "http://fake-ha:8123"
os.environ["HA_API_TOKEN"] = "test-token-123"

from ha_bridge.client import HABridgeClient
from scene_engine.engine import SceneEngine, _cast_value, _evaluate_condition, _extract_value, _validate_scene_structure
from scene_engine.ha_transport import HATransport


# ── HATransport tests ──────────────────────────────────────────────


@pytest.fixture
def mock_ha_client():
    """Create a HABridgeClient with mocked internal methods."""
    c = HABridgeClient(base_url="http://fake-ha:8123", token="test-token-123")
    c.get_entity_state = AsyncMock()
    c.get_all_states = AsyncMock()
    c.call_service = AsyncMock()
    c.connect_ws = AsyncMock(return_value=True)
    c.subscribe_events = AsyncMock(return_value=42)
    c.close = AsyncMock()
    return c


@pytest.fixture
def transport(mock_ha_client):
    return HATransport(mock_ha_client)


@pytest.mark.asyncio
async def test_transport_start(transport, mock_ha_client):
    ok = await transport.start()
    assert ok is True
    mock_ha_client.connect_ws.assert_called_once()


@pytest.mark.asyncio
async def test_transport_start_ws_fails(transport, mock_ha_client):
    mock_ha_client.connect_ws.return_value = False
    ok = await transport.start()
    assert ok is False


@pytest.mark.asyncio
async def test_get_device_state(transport, mock_ha_client):
    mock_ha_client.get_entity_state.return_value = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {"brightness": 255},
    }
    state = await transport.get_device_state("light.living_room")
    assert state is not None
    assert state["state"] == "on"
    mock_ha_client.get_entity_state.assert_called_with("light.living_room")


@pytest.mark.asyncio
async def test_get_device_states(transport, mock_ha_client):
    mock_ha_client.get_entity_state.side_effect = [
        {"entity_id": "light.a", "state": "on"},
        {"entity_id": "light.b", "state": "off"},
    ]
    states = await transport.get_device_states(["light.a", "light.b"])
    assert states["light.a"]["state"] == "on"
    assert states["light.b"]["state"] == "off"


@pytest.mark.asyncio
async def test_get_device_states_empty(transport, mock_ha_client):
    states = await transport.get_device_states([])
    assert states == {}
    mock_ha_client.get_entity_state.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action(transport, mock_ha_client):
    mock_ha_client.call_service.return_value = [{"entity_id": "light.test", "state": "on"}]
    result = await transport.execute_action("light", "turn_on", {"entity_id": "light.test"})
    assert result == [{"entity_id": "light.test", "state": "on"}]
    mock_ha_client.call_service.assert_called_with("light", "turn_on", {"entity_id": "light.test"})


@pytest.mark.asyncio
async def test_subscribe_device_changes_no_ws(transport, mock_ha_client):
    transport._ws_connected = False
    called = []
    ok = await transport.subscribe_device_changes(["light.a"], lambda e: called.append(e))
    assert ok is False
    assert called == []


@pytest.mark.asyncio
async def test_subscribe_device_changes_ok(transport, mock_ha_client):
    transport._ws_connected = True
    called = []
    ok = await transport.subscribe_device_changes(["light.a"], lambda e: called.append(e))
    assert ok is True
    mock_ha_client.subscribe_events.assert_called_once()


@pytest.mark.asyncio
async def test_transport_close(transport, mock_ha_client):
    await transport.close()
    mock_ha_client.close.assert_called_once()
    assert transport._ws_connected is False


# ── Condition evaluation helpers ──────────────────────────────────


def test_extract_value_state():
    s = {"entity_id": "sensor.t", "state": "24.5", "attributes": {"unit": "°C"}}
    assert _extract_value(s, None) == "24.5"
    assert _extract_value(s, "state") == "24.5"
    assert _extract_value(s, "unit") == "°C"


def test_cast_value():
    assert _cast_value("on", True) is True
    assert _cast_value("off", True) is False
    assert _cast_value("42", 0) == 42
    assert _cast_value("3.14", 0.0) == 3.14
    assert _cast_value("hello", []) == "hello"


def test_evaluate_condition_eq():
    state = {"entity_id": "sensor.t", "state": "24.5"}
    assert _evaluate_condition({"attribute": "state", "operator": "eq", "value": "24.5"}, state) is True
    assert _evaluate_condition({"attribute": "state", "operator": "eq", "value": "30"}, state) is False


def test_evaluate_condition_gt():
    state = {"entity_id": "sensor.t", "state": "28", "attributes": {}}
    assert _evaluate_condition({"attribute": "state", "operator": "gt", "value": 25}, state) is True
    assert _evaluate_condition({"attribute": "state", "operator": "gt", "value": 30}, state) is False


def test_evaluate_condition_lt():
    state = {"entity_id": "sun.sun", "state": "above_horizon", "attributes": {"elevation": 5}}
    assert _evaluate_condition({"attribute": "elevation", "operator": "lt", "value": 10}, state) is True
    assert _evaluate_condition({"attribute": "elevation", "operator": "lt", "value": 0}, state) is False


def test_evaluate_condition_between():
    state = {"entity_id": "sensor.t", "state": "24"}
    assert _evaluate_condition({"attribute": "state", "operator": "between", "value": [20, 30]}, state) is True
    assert _evaluate_condition({"attribute": "state", "operator": "between", "value": [30, 40]}, state) is False


def test_evaluate_condition_ne():
    state = {"entity_id": "binary_sensor.d", "state": "off"}
    assert _evaluate_condition({"attribute": "state", "operator": "ne", "value": "on"}, state) is True


def test_evaluate_condition_unknown_operator():
    state = {"entity_id": "sensor.t", "state": "24"}
    result = _evaluate_condition({"attribute": "state", "operator": "foobar", "value": "24"}, state)
    assert result is False


# ── Rule file loading ─────────────────────────────────────────────


SAMPLE_RULES_YAML = """
scenes:
  - name: "回家模式"
    enabled: true
    trigger:
      entity_id: "binary_sensor.door_sensor"
      from: "off"
      to: "on"
    conditions:
      - entity_id: "sun.sun"
        attribute: "elevation"
        operator: "lt"
        value: 10
    actions:
      - domain: "light"
        service: "turn_on"
        target:
          entity_id: "light.living_room"

  - name: "夜间制冷"
    enabled: false
    trigger:
      entity_id: "sensor.indoor_temperature"
    conditions:
      - entity_id: "sensor.indoor_temperature"
        attribute: "state"
        operator: "gt"
        value: 28
    actions:
      - domain: "climate"
        service: "set_temperature"
        target:
          entity_id: "climate.bedroom_ac"
        data:
          temperature: 24
"""


@pytest.fixture
def rules_dir(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "test.yaml").write_text(SAMPLE_RULES_YAML, encoding="utf-8")
    return str(d)


@pytest.fixture
def engine(mock_ha_client):
    transport = HATransport(mock_ha_client)
    return SceneEngine(transport, rules_dir=None, poll_interval=0.1)


@pytest.mark.asyncio
async def test_load_rules(engine, rules_dir):
    count = await engine.load_rules(rules_dir)
    assert count == 2
    assert engine.scene_count() == 2

    scenes = engine.list_scenes()
    names = {s["name"] for s in scenes}
    assert "回家模式" in names
    assert "夜间制冷" in names


@pytest.mark.asyncio
async def test_load_rules_missing_dir(engine):
    count = await engine.load_rules("/nonexistent/path")
    assert count == 0


@pytest.mark.asyncio
async def test_engine_start_stop(engine, rules_dir):
    await engine.load_rules(rules_dir)
    ok = await engine.start()
    assert ok is True

    await asyncio.sleep(0.2)  # let loop run once
    await engine.stop()


@pytest.mark.asyncio
async def test_trigger_scene_manually(engine, rules_dir, mock_ha_client):
    mock_ha_client.call_service.return_value = [{"entity_id": "light.living_room", "state": "on"}]

    await engine.load_rules(rules_dir)
    ok = await engine.trigger_scene_manually("回家模式")
    assert ok is True
    mock_ha_client.call_service.assert_called()


@pytest.mark.asyncio
async def test_trigger_scene_manually_not_found(engine, rules_dir, mock_ha_client):
    await engine.load_rules(rules_dir)
    ok = await engine.trigger_scene_manually("不存在的场景")
    assert ok is False


@pytest.mark.asyncio
async def test_scene_evaluation_trigger_match(engine, rules_dir, mock_ha_client):
    """Simulate a full poll cycle: door sensor matches trigger → conditions pass → action fires."""
    mock_ha_client.get_entity_state.side_effect = [
        {"entity_id": "binary_sensor.door_sensor", "state": "on",
         "attributes": {}, "last_changed": "", "last_updated": ""},
        {"entity_id": "sun.sun", "state": "below_horizon",
         "attributes": {"elevation": 5}, "last_changed": "", "last_updated": ""},
    ]
    mock_ha_client.call_service.return_value = [{"entity_id": "light.living_room", "state": "on"}]

    await engine.load_rules(rules_dir)
    engine._running = False

    scenes = engine._scenes
    home_scene = [s for s in scenes if s["name"] == "回家模式"][0]
    await engine._evaluate_scene(home_scene)

    mock_ha_client.call_service.assert_called()
    call_args = mock_ha_client.call_service.call_args
    assert call_args[0][0] == "light"
    assert call_args[0][1] == "turn_on"


@pytest.mark.asyncio
async def test_scene_evaluation_trigger_not_met(engine, rules_dir, mock_ha_client):
    """Door sensor is off → trigger not met → no action."""
    mock_ha_client.get_entity_state.return_value = {
        "entity_id": "binary_sensor.door_sensor", "state": "off",
        "attributes": {}, "last_changed": "", "last_updated": "",
    }

    await engine.load_rules(rules_dir)
    scenes = engine._scenes
    home_scene = [s for s in scenes if s["name"] == "回家模式"][0]
    await engine._evaluate_scene(home_scene)

    mock_ha_client.call_service.assert_not_called()


@pytest.mark.asyncio
async def test_scene_evaluation_condition_fails(engine, rules_dir, mock_ha_client):
    """Door matches trigger but sun elevation > 10 → condition fails → no action."""
    mock_ha_client.get_entity_state.side_effect = [
        {"entity_id": "binary_sensor.door_sensor", "state": "on",
         "attributes": {}, "last_changed": "", "last_updated": ""},
        {"entity_id": "sun.sun", "state": "above_horizon",
         "attributes": {"elevation": 45}, "last_changed": "", "last_updated": ""},
    ]

    await engine.load_rules(rules_dir)
    scenes = engine._scenes
    home_scene = [s for s in scenes if s["name"] == "回家模式"][0]
    await engine._evaluate_scene(home_scene)

    mock_ha_client.call_service.assert_not_called()


# ── Dry-run trigger logic ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_ws_state_change_trigger(engine, rules_dir, mock_ha_client):
    """Simulate a WebSocket state_changed event triggering a scene."""
    mock_ha_client.get_entity_state.return_value = {
        "entity_id": "sun.sun", "state": "below_horizon",
        "attributes": {"elevation": 5}, "last_changed": "", "last_updated": "",
    }
    mock_ha_client.call_service.return_value = [{"entity_id": "light.living_room", "state": "on"}]

    await engine.load_rules(rules_dir)

    event = {
        "event": {
            "event_type": "state_changed",
            "data": {
                "entity_id": "binary_sensor.door_sensor",
                "old_state": {"entity_id": "binary_sensor.door_sensor", "state": "off"},
                "new_state": {"entity_id": "binary_sensor.door_sensor", "state": "on"},
            },
        }
    }
    await engine._on_device_state_changed(event)

    mock_ha_client.call_service.assert_called()


# ── Validation ────────────────────────────────────────────────────


def test_validate_scene_valid():
    scene = {
        "name": "测试场景",
        "trigger": {"entity_id": "sensor.test"},
        "actions": [{"domain": "light", "service": "turn_on", "target": {}}],
    }
    assert _validate_scene_structure(scene) == []


def test_validate_scene_missing_name():
    scene = {
        "trigger": {},
        "actions": [{"domain": "light", "service": "turn_on"}],
    }
    errors = _validate_scene_structure(scene)
    assert any("name" in e for e in errors)


def test_validate_scene_missing_domain():
    scene = {
        "name": "x",
        "trigger": {"entity_id": "sensor.a"},
        "actions": [{"service": "turn_on"}],
    }
    errors = _validate_scene_structure(scene)
    assert any("domain" in e for e in errors)


def test_validate_scene_missing_service():
    scene = {
        "name": "x",
        "trigger": {"entity_id": "sensor.a"},
        "actions": [{"domain": "light"}],
    }
    errors = _validate_scene_structure(scene)
    assert any("service" in e for e in errors)


def test_list_scenes(engine, rules_dir):
    import asyncio
    asyncio.run(engine.load_rules(rules_dir))
    scenes = engine.list_scenes()
    assert len(scenes) == 2
    for s in scenes:
        assert "_source_file" not in s
        assert "_state" in s
