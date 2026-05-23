"""
HA Transport — Scene Engine adapter over HABridgeClient.

Translates the scene engine's device-state queries and action-execution
requests into Home Assistant REST API calls and WebSocket subscriptions.

All public methods are async. Failures are logged and None / empty results
returned so the scene engine can degrade gracefully.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from ha_bridge.client import HABridgeClient

logger = logging.getLogger(__name__)

# Convenience re-export for consumers that import from ha_transport directly
HABridgeClient = HABridgeClient  # type: ignore


class HATransport:
    """Scene Engine transport adapter for Home Assistant.

    Wraps HABridgeClient with semantics tailored to the scene engine:
      - get_device_state(): fetch a single entity's state dict
      - get_device_states(): batch-fetch multiple entity states
      - execute_action(): call HA service (light.turn_on, switch.turn_off, etc.)
      - subscribe_device_changes(): WebSocket subscription for state_changed events

    Usage::

        from ha_bridge.client import HABridgeClient
        from scene_engine.ha_transport import HATransport

        ha = HABridgeClient()
        transport = HATransport(ha)
        await transport.start()

        state = await transport.get_device_state("light.living_room")
        await transport.execute_action("light", "turn_on", {"entity_id": "light.living_room"})
    """

    def __init__(self, ha_client: HABridgeClient):
        self.ha = ha_client
        self._ws_connected = False
        self._state_listeners: Dict[str, List[Callable]] = {}
        # event_type → list of (entity_ids, callback)
        self._event_subscriptions: Dict[str, Callable] = {}

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> bool:
        """Connect WebSocket and prepare for device change subscriptions.

        Returns True on success. If WS fails, the transport still works in
        polling-only mode (get_device_state / execute_action over REST).
        """
        self._ws_connected = await self.ha.connect_ws()
        if self._ws_connected:
            logger.info("HATransport started (WS active)")
        else:
            logger.warning("HATransport started without WebSocket — polling-only mode")
        return self._ws_connected

    async def close(self):
        """Shut down underlying HA client connections."""
        await self.ha.close()
        self._ws_connected = False
        self._state_listeners.clear()
        self._event_subscriptions.clear()

    # ── Device State ────────────────────────────────────────────────────

    async def get_device_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve current state for a single HA entity.

        Returns the full entity state dict (keys: entity_id, state, attributes,
        last_changed, last_updated) or None when unreachable / not found.
        """
        return await self.ha.get_entity_state(entity_id)

    async def get_device_states(self, entity_ids: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """Batch-fetch states for multiple entities.

        Returns a dict mapping entity_id → state_dict (or None per entity).
        Uses individual requests rather than get_all_states() to limit payload
        size when the HA instance has thousands of entities.
        """
        if not entity_ids:
            return {}

        async def _fetch_one(eid: str):
            return eid, await self.ha.get_entity_state(eid)

        tasks = [_fetch_one(eid) for eid in entity_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: Dict[str, Optional[Dict[str, Any]]] = {}
        for item in results:
            if isinstance(item, Exception):
                logger.warning("Batch fetch error: %s", item)
                continue
            eid, state = item
            out[eid] = state
        return out

    # ── Action Execution ────────────────────────────────────────────────

    async def execute_action(
        self,
        domain: str,
        service: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute a HA service call (device control).

        Example:
            await transport.execute_action("light", "turn_on",
                                           {"entity_id": "light.living_room", "brightness": 255})
        """
        return await self.ha.call_service(domain, service, data)

    # ── Event Subscription ──────────────────────────────────────────────

    async def subscribe_device_changes(
        self,
        entity_ids: List[str],
        callback: Callable[[Dict[str, Any]], Any],
    ) -> bool:
        """Subscribe to state_changed events for specified entities.

        callback receives the raw HA event dict. It is called for every
        state_changed event whose entity_id is in entity_ids.

        Returns True if subscription succeeded, False otherwise.
        """
        if not self._ws_connected:
            logger.warning("subscribe_device_changes called but WS is not connected")
            return False

        entity_set = set(entity_ids)

        async def _filtered_callback(event: Dict[str, Any]):
            data = event.get("event", {}).get("data", {})
            eid = data.get("entity_id", "")
            if eid in entity_set:
                try:
                    ret = callback(event)
                    if asyncio.iscoroutine(ret):
                        await ret
                except Exception:
                    logger.exception("Device change callback failed for %s", eid)

        sub_id = await self.ha.subscribe_events("state_changed", _filtered_callback)
        if sub_id is not None:
            self._event_subscriptions[f"device_changes:{','.join(sorted(entity_ids))}"] = _filtered_callback
            logger.info("Subscribed to device changes for %d entities", len(entity_ids))
            return True

        return False

    # ── Convenience ─────────────────────────────────────────────────────

    async def get_all_device_states(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch ALL entity states from HA in one call.

        Use sparingly — this can be a large payload. Prefer get_device_state()
        or get_device_states() when the entity IDs are known.
        """
        return await self.ha.get_all_states()

    async def is_connected(self) -> bool:
        """Return whether the WebSocket transport is currently active."""
        return self._ws_connected
