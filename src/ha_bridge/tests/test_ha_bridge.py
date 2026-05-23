"""
Tests for ha_bridge.client — using pytest + aioresponses to mock HA REST API
and a custom mock for WebSocket handshake.

Run: python -m pytest src/ha_bridge/tests/ -v
"""

import asyncio
import json
import os

import aiohttp
import pytest
from aioresponses import aioresponses

# Force-env so the client constructor works without real values.
os.environ["HA_API_URL"] = "http://fake-ha:8123"
os.environ["HA_API_TOKEN"] = "test-token-123"

from ha_bridge.client import (
    HAAuthError,
    HABridgeClient,
    HABridgeError,
    HAConnectionError,
)

BASE = "http://fake-ha:8123"


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return HABridgeClient(base_url=BASE, token="test-token-123")


@pytest.fixture
def sample_states():
    return [
        {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "客厅灯", "supported_features": 1},
            "last_changed": "2026-05-23T00:00:00+00:00",
            "last_updated": "2026-05-23T00:00:00+00:00",
        },
        {
            "entity_id": "sensor.temperature",
            "state": "24.5",
            "attributes": {"friendly_name": "温度", "unit_of_measurement": "°C"},
            "last_changed": "2026-05-23T00:00:00+00:00",
            "last_updated": "2026-05-23T00:00:00+00:00",
        },
    ]


# ── Constructor tests ─────────────────────────────────────────────────


def test_constructor_from_env():
    c = HABridgeClient()
    assert c._base_url == BASE
    assert c._token == "test-token-123"


def test_constructor_explicit_args():
    c = HABridgeClient(base_url="http://x:8123", token="abc")
    assert c._base_url == "http://x:8123"
    assert c._token == "abc"


def test_constructor_missing_url(monkeypatch):
    monkeypatch.delenv("HA_API_URL", raising=False)
    with pytest.raises(HABridgeError, match="HA_API_URL"):
        HABridgeClient(base_url="", token="x")


def test_constructor_missing_token(monkeypatch):
    monkeypatch.delenv("HA_API_TOKEN", raising=False)
    with pytest.raises(HABridgeError, match="HA_API_TOKEN"):
        HABridgeClient(base_url="http://x", token="")


# ── get_all_states ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_all_states_ok(client, sample_states):
    with aioresponses() as m:
        m.get(f"{BASE}/api/states", payload=sample_states, status=200)

        result = await client.get_all_states()
        assert result == sample_states
        assert len(result) == 2

    await client.close()


@pytest.mark.asyncio
async def test_get_all_states_403(client):
    with aioresponses() as m:
        m.get(f"{BASE}/api/states", status=403)

        result = await client.get_all_states()
        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_get_all_states_connection_refused(client):
    with aioresponses() as m:
        m.get(f"{BASE}/api/states", exception=aiohttp.ClientConnectionError())

        result = await client.get_all_states()
        assert result is None

    await client.close()


# ── get_entity_state ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_entity_state_ok(client, sample_states):
    with aioresponses() as m:
        m.get(f"{BASE}/api/states/light.living_room", payload=sample_states[0], status=200)

        result = await client.get_entity_state("light.living_room")
        assert result["entity_id"] == "light.living_room"
        assert result["state"] == "on"

    await client.close()


@pytest.mark.asyncio
async def test_get_entity_state_not_found(client):
    with aioresponses() as m:
        m.get(f"{BASE}/api/states/light.nonexistent", status=404)

        result = await client.get_entity_state("light.nonexistent")
        assert result is None

    await client.close()


# ── call_service ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_service_ok(client):
    response = [{"entity_id": "light.living_room", "state": "on"}]
    with aioresponses() as m:
        m.post(f"{BASE}/api/services/light/turn_on", payload=response, status=200)

        result = await client.call_service(
            "light", "turn_on", {"entity_id": "light.living_room", "brightness": 255}
        )
        assert result == response

    await client.close()


@pytest.mark.asyncio
async def test_call_service_empty_domain(client):
    result = await client.call_service("", "turn_on")
    assert result is None

    await client.close()


# ── get_services ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_services_ok(client):
    services_payload = [
        {
            "domain": "light",
            "services": {
                "turn_on": {
                    "name": "Turn on",
                    "fields": {
                        "entity_id": {"example": "light.living_room"},
                        "brightness": {"example": 255},
                    },
                }
            },
        }
    ]
    with aioresponses() as m:
        m.get(f"{BASE}/api/services", payload=services_payload, status=200)

        result = await client.get_services()
        assert result == services_payload
        assert result[0]["domain"] == "light"

    await client.close()


# ── WebSocket mock helpers ────────────────────────────────────────────


class FakeWS:
    """Minimal fake aiohttp WebSocket response for connect_ws handshake."""

    def __init__(self, messages):
        self._messages = messages
        self._pos = 0
        self.closed = False

    async def receive_json(self):
        if self._pos >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._pos]
        self._pos += 1
        return msg

    async def send_json(self, data):
        pass  # silently accept

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self


class FakeSession:
    """Session that returns our FakeWS for ws_connect."""

    def __init__(self, ws):
        self._ws = ws
        self.closed = False

    async def ws_connect(self, url):
        return self._ws

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_connect_ws_ok(client):
    # Skip aioresponses for this test — patch _ensure_session manually
    ws = FakeWS([
        {"type": "auth_required", "ha_version": "2025.1"},
        {"type": "auth_ok", "ha_version": "2025.1"},
    ])
    client._session = FakeSession(ws)
    client._ws = None

    ok = await client.connect_ws()
    assert ok is True
    assert client._ws is not None

    await client.close()


@pytest.mark.asyncio
async def test_connect_ws_auth_fail(client):
    ws = FakeWS([
        {"type": "auth_required"},
        {"type": "auth_invalid", "message": "Invalid token"},
    ])
    client._session = FakeSession(ws)

    ok = await client.connect_ws()
    assert ok is False

    await client.close()


@pytest.mark.asyncio
async def test_subscribe_events_without_ws(client):
    # No WS connection → should return None
    results = []
    result = await client.subscribe_events("state_changed", lambda e: results.append(e))
    assert result is None

    await client.close()
