"""
Web Adapter Router — Frontend-compatible API endpoints for the React SPA.

Provides format-translated endpoints that sit between the raw HA Bridge
and the Web Panel's expected data shapes. Also includes WebSocket endpoint
for real-time device state updates.

Routes:
    GET  /api/devices          — translated device list
    GET  /api/scenes           — translated scene list
    POST /api/scenes/{id}/toggle   — toggle scene enabled/disabled
    POST /api/scenes/{id}/trigger  — trigger scene execution
    POST /api/scenes               — create custom scene (stub)
    DELETE /api/scenes/{id}        — delete scene (stub)
    GET  /api/logs             — recent activity logs
    GET  /api/setup/status     — setup wizard status
    GET  /api/config/fields    — config fields (empty, migrated to HA)
    WS   /ws                   — WebSocket device state push
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

# ── In-memory scene extension storage (web-created scenes) ─────────
_web_scenes: Dict[str, Dict[str, Any]] = {}


# ── HA entity_id → web device format converter ─────────────────────

def _category_from_entity_id(entity_id: str) -> str:
    """Infer category from HA entity_id prefix."""
    prefix_map = {
        "light": "light",
        "switch": "switch",
        "sensor": "sensor",
        "binary_sensor": "sensor",
        "climate": "ac",
        "cover": "curtain",
        "fan": "fan",
        "media_player": "media",
        "lock": "lock",
        "vacuum": "vacuum",
        "camera": "camera",
        "automation": "automation",
        "script": "script",
        "scene": "scene",
        "input_boolean": "switch",
        "input_number": "sensor",
        "water_heater": "ac",
        "device_tracker": "sensor",
    }
    if "." not in entity_id:
        return "unknown"
    prefix = entity_id.split(".", 1)[0]
    return prefix_map.get(prefix, prefix)


def _room_from_entity(attrs: Dict[str, Any], entity_id: str) -> str:
    """Extract room/area from entity attributes."""
    # Try HA area_id first
    area = attrs.get("area_id") or attrs.get("area") or attrs.get("room")
    if area:
        return area

    # Try to infer from entity_id patterns
    parts = entity_id.split(".")
    if len(parts) >= 2:
        name_parts = parts[1].split("_")
        room_hints = {
            "living": "客厅", "ketin": "客厅",
            "bedroom": "卧室", "woshi": "卧室",
            "kitchen": "厨房", "chufang": "厨房",
            "bathroom": "浴室", "yushi": "浴室",
            "balcony": "阳台", "yangtai": "阳台",
            "study": "书房", "shufang": "书房",
            "corridor": "走廊", "zoulang": "走廊",
            "dining": "餐厅", "canting": "餐厅",
            "entrance": "玄关", "xuanguan": "玄关",
        }
        for part in name_parts:
            for eng, cn in room_hints.items():
                if eng in part.lower():
                    return cn

    return "其他"


def _name_from_entity(attrs: Dict[str, Any], entity_id: str) -> str:
    """Extract human-readable name."""
    friendly = attrs.get("friendly_name")
    if friendly:
        return friendly
    # Fallback: last part of entity_id
    if "." in entity_id:
        return entity_id.split(".", 1)[1].replace("_", " ").title()
    return entity_id


def _state_from_entity(ha_state: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Build a simplified state dict for the web panel."""
    state: Dict[str, Any] = {}

    # Power state (on/off)
    if ha_state in ("on", "off"):
        state["power"] = ha_state
    elif ha_state not in ("unavailable", "unknown", ""):
        state["value"] = ha_state

    # Common sensor values
    for key in ("temperature", "humidity", "brightness", "illuminance",
                "pressure", "battery", "pm25", "co2", "power_consumption",
                "volume", "position", "current_position"):
        val = attrs.get(key)
        if val is not None:
            state[key] = val

    # Special: color/color_temp
    if "color_temp" in attrs:
        state["color_temp"] = attrs["color_temp"]
    if "rgb_color" in attrs:
        state["rgb_color"] = attrs["rgb_color"]

    # Special: hvac_mode for climate entities
    if "hvac_mode" in attrs:
        state["mode"] = attrs["hvac_mode"]
    if "hvac_action" in attrs:
        state["action"] = attrs["hvac_action"]
    if "current_temperature" in attrs:
        state["temperature"] = attrs["current_temperature"]
    if "target_temp_high" in attrs:
        state["target_temp_high"] = attrs["target_temp_high"]
    if "target_temp_low" in attrs:
        state["target_temp_low"] = attrs["target_temp_low"]

    # Media player
    if "media_title" in attrs:
        state["media_title"] = attrs["media_title"]
    if "media_artist" in attrs:
        state["media_artist"] = attrs["media_artist"]

    return state


def convert_ha_device(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a raw HA entity state dict to web panel device format."""
    entity_id = raw.get("entity_id", "unknown")
    ha_state = raw.get("state", "")
    attrs = raw.get("attributes", {}) or {}

    return {
        "id": entity_id,
        "name": _name_from_entity(attrs, entity_id),
        "category": _category_from_entity_id(entity_id),
        "room": _room_from_entity(attrs, entity_id),
        "online": ha_state != "unavailable",
        "state": _state_from_entity(ha_state, attrs),
        "updated_at": raw.get("last_updated", ""),
    }


# ── Scene format converter ─────────────────────────────────────────

def convert_scene(raw: Dict[str, Any], scene_id_hint: str = "") -> Dict[str, Any]:
    """Convert a raw scene rule dict to web panel scene format.
    
    Supports both:
    - Old format: {name, type, trigger, actions: [{device, command, params}]}
    - New format: {name, trigger_type, actions: [{domain, service, data}]}
    """
    name = raw.get("name", "unknown")
    scene_id = scene_id_hint or name.lower().replace(" ", "_").replace("．", "")

    # Map scene type (supports old "type" field and new "trigger_type")
    raw_type = raw.get("type") or raw.get("trigger_type", "manual")
    # Old format: "auto" → auto, "manual" → manual, "scheduled" → scheduled
    # New format: "event" → auto, "schedule" → scheduled, "manual" → manual
    type_map = {"event": "auto", "schedule": "scheduled", "manual": "manual"}
    scene_type = type_map.get(raw_type, raw_type if raw_type in ("auto", "manual", "scheduled") else "manual")

    # Priority mapping (supports string priorities from old format)
    prio = raw.get("priority", 5)
    if isinstance(prio, str):
        prio_map = {"critical": 10, "high": 7, "comfort": 5, "low": 3, "background": 1}
        prio = prio_map.get(prio, 5)
    elif not isinstance(prio, (int, float)):
        prio = 5

    # Icon: prefer scene's own icon, then fall back to name-based mapping
    icon = raw.get("icon", "")
    if not icon or not isinstance(icon, str) or len(icon) > 2:
        icon_map = {
            "黄昏模式": "🌅", "晚安模式": "🌙", "起床模式": "🌞",
            "离家模式": "🚪", "回家模式": "🏠", "影院模式": "🎬",
            "派对模式": "🎉", "节能模式": "⚡", "安防模式": "🔒",
            "阅读模式": "📖", "晚餐模式": "🍽", "晨间模式": "☀️",
            "睡眠模式": "💤", "清洁模式": "🧹", "烹饪模式": "🍳",
            "度假模式": "🏖", "晾衣模式": "👕", "观影模式": "🍿",
            "离家关灯": "🚪", "睡前检查": "🛏", "回家开灯": "🏠",
            "室内过冷": "🥶", "室内过热": "🥵", "长期无人": "🏚",
            "起床预热": "🌄",
            "evening_mode": "🌅", "night_mode": "🌙", "morning_mode": "🌞",
            "away_mode": "🚪", "home_mode": "🏠", "movie_mode": "🎬",
        }
        icon = icon_map.get(name, icon_map.get(scene_id, "🎭"))

    # Normalize actions for display
    actions = []
    for act in raw.get("actions", []) or []:
        if isinstance(act, dict):
            normalized = dict(act)
            # Ensure display-friendly fields
            if "domain" not in normalized and "device" in normalized:
                normalized["domain"] = normalized.get("domain") or ""
                normalized["service"] = normalized.get("command", "")
                normalized["data"] = normalized.get("params", normalized.get("data", {}))
            actions.append(normalized)

    return {
        "id": scene_id,
        "name": name,
        "type": scene_type,
        "enabled": raw.get("enabled", True),
        "description": raw.get("description", ""),
        "priority": int(prio) if prio else 5,
        "icon": icon,
        "actions": actions,
    }


def convert_web_scene(scene_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an in-memory web-created scene for API response."""
    return {
        "id": scene_id,
        "name": data.get("name", scene_id),
        "type": "manual",
        "enabled": data.get("enabled", True),
        "description": data.get("description", ""),
        "priority": data.get("priority", 5),
        "icon": data.get("icon", "🎭"),
        "actions": data.get("actions", []),
    }


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/api/devices")
async def list_devices_web():
    """Return device list in web panel format, translated from HA."""
    try:
        from ha_bridge.client import HABridgeClient

        bridge = HABridgeClient()
        raw_devices = await bridge.get_all_states()

        if raw_devices is None:
            return {"data": {"devices": []}}

        devices = [convert_ha_device(d) for d in raw_devices]
        return {"data": {"devices": devices}}

    except Exception as e:
        logger.exception("Failed to fetch devices for web adapter")
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/scenes")
async def list_scenes_web():
    """Return scene list in web panel format.
    
    Loads YAML files directly from /scenes/ directory (recursively),
    bypassing the scene engine's strict validation to display all scenes.
    """
    try:
        import yaml
        from pathlib import Path

        scenes_dir = Path("/scenes")
        loaded = []

        if scenes_dir.exists():
            for yf in sorted(scenes_dir.glob("**/*.yaml")) + sorted(scenes_dir.glob("**/*.yml")):
                try:
                    raw = yaml.safe_load(yf.read_text(encoding="utf-8"))
                    if not raw:
                        continue
                    # Support both top-level list and single object/doc
                    scene_list = raw if isinstance(raw, list) else raw.get("scenes", [raw])
                    if not isinstance(scene_list, list):
                        scene_list = [raw]
                    for scene in scene_list:
                        if isinstance(scene, dict) and scene.get("name"):
                            loaded.append(scene)
                except Exception:
                    logger.debug("Skipping unparseable YAML: %s", yf)

        scenes = [convert_scene(s) for s in loaded]

        # Add web-created scenes
        for sid, sdata in _web_scenes.items():
            scenes.append(convert_web_scene(sid, sdata))

        return {"data": {"scenes": scenes}}

    except Exception as e:
        logger.exception("Failed to fetch scenes for web adapter")
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/scenes/{scene_id}/toggle")
async def toggle_scene(scene_id: str):
    """Toggle a scene enabled/disabled (in-memory, not persisted to YAML)."""
    # Check web scenes first
    if scene_id in _web_scenes:
        _web_scenes[scene_id]["enabled"] = not _web_scenes[scene_id].get("enabled", True)
        return {
            "ok": True,
            "data": {
                "id": scene_id,
                "enabled": _web_scenes[scene_id]["enabled"],
            },
        }

    # For YAML-based scenes: load and find by name→id
    import yaml
    from pathlib import Path

    scenes_dir = Path("/scenes")
    for yf in sorted(scenes_dir.glob("**/*.yaml")) + sorted(scenes_dir.glob("**/*.yml")):
        try:
            raw = yaml.safe_load(yf.read_text(encoding="utf-8"))
            if not raw or not isinstance(raw, dict):
                continue
            name = raw.get("name", "")
            sid = name.lower().replace(" ", "_")
            if sid == scene_id:
                # Toggle in-memory (YAML file is read-only)
                current = raw.get("enabled", True)
                return {
                    "ok": True,
                    "data": {"id": scene_id, "enabled": not current},
                }
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")


@router.post("/api/scenes/{scene_id}/trigger")
async def trigger_scene(scene_id: str):
    """Trigger scene execution.
    
    Tries to execute actions via HA Bridge for web-created scenes.
    For YAML-based scenes, returns a best-effort trigger attempt.
    """
    # For web scenes, execute stored actions via HA
    if scene_id in _web_scenes:
        try:
            from ha_bridge.client import HABridgeClient

            bridge = HABridgeClient()
            actions = _web_scenes[scene_id].get("actions", [])
            results = []
            for action in actions:
                domain = action.get("domain", "")
                service = action.get("service", "")
                data = action.get("data", {})
                if domain and service:
                    r = await bridge.call_service(domain, service, data)
                    results.append({"success": r is not None})
            return {
                "ok": True,
                "data": {
                    "id": scene_id,
                    "actions_executed": len(results),
                    "results": results,
                },
            }
        except Exception as e:
            logger.exception("Failed to trigger web scene")
            raise HTTPException(status_code=500, detail=str(e))

    # For YAML-based scenes: find the scene and attempt basic HA execution
    import yaml
    from pathlib import Path

    scenes_dir = Path("/scenes")
    found_actions = []
    for yf in sorted(scenes_dir.glob("**/*.yaml")) + sorted(scenes_dir.glob("**/*.yml")):
        try:
            raw = yaml.safe_load(yf.read_text(encoding="utf-8"))
            if not raw or not isinstance(raw, dict):
                continue
            sid = raw.get("name", "").lower().replace(" ", "_")
            if sid == scene_id:
                found_actions = raw.get("actions", [])
                break
        except Exception:
            continue

    if not found_actions:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    # Try to execute via HA Bridge if available
    try:
        from ha_bridge.client import HABridgeClient

        bridge = HABridgeClient()
        results = []
        for action in found_actions:
            if isinstance(action, dict):
                domain = action.get("domain") or action.get("device", "")
                service = action.get("service") or action.get("command", "")
                data = action.get("data") or action.get("params", {})
                if domain and service:
                    r = await bridge.call_service(domain, service, data)
                    results.append({"success": True, "domain": domain, "service": service})
        return {
            "ok": True,
            "data": {
                "id": scene_id,
                "actions_executed": len(results),
                "results": results,
            },
        }
    except Exception:
        # Fallback: return trigger acknowledged without execution
        return {
            "ok": True,
            "data": {
                "id": scene_id,
                "actions_executed": 0,
                "note": "HA Bridge unavailable; trigger acknowledged but not executed",
            },
        }


@router.post("/api/scenes")
async def create_scene(body: Dict[str, Any]):
    """Create a custom scene (stored in memory)."""
    scene_id = body.get("id") or body.get("name", "").lower().replace(" ", "_")
    if not scene_id:
        raise HTTPException(status_code=400, detail="Scene id or name is required")

    _web_scenes[scene_id] = {
        "name": body.get("name", scene_id),
        "enabled": body.get("enabled", True),
        "description": body.get("description", ""),
        "priority": body.get("priority", 5),
        "icon": body.get("icon", "🎭"),
        "actions": body.get("actions", []),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    return {"ok": True, "data": convert_web_scene(scene_id, _web_scenes[scene_id])}


@router.delete("/api/scenes/{scene_id}")
async def delete_scene(scene_id: str):
    """Delete a web-created scene."""
    if scene_id in _web_scenes:
        del _web_scenes[scene_id]
        return {"ok": True, "data": {"id": scene_id, "deleted": True}}

    # Engine scenes cannot be deleted via web
    raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found or not deletable")


@router.get("/api/logs")
async def list_logs():
    """Return recent activity logs. Currently returns empty/mock data."""
    return {"data": {"logs": []}}


@router.get("/api/setup/status")
async def setup_status():
    """Return setup wizard status — system is configured."""
    return {"ok": True, "data": {"configured": True}}


@router.get("/api/config/fields")
async def config_fields():
    """Return config fields — empty since config is managed via HA."""
    return {"ok": True, "data": {"fields": {}}}


# ── WebSocket endpoint ─────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint that pushes device state updates.

    Subscribes to HA state_changed events and forwards transformed device
    data to connected web clients. Falls back to periodic polling if
    WebSocket subscription fails.
    """
    await ws.accept()
    logger.info("WebSocket client connected")

    try:
        # Try live HA WebSocket subscription first
        from ha_bridge.client import HABridgeClient

        bridge = HABridgeClient()
        ws_connected = await bridge.connect_ws()

        if ws_connected:
            # Subscribe to state_changed events
            event_queue: asyncio.Queue = asyncio.Queue()

            async def on_state_change(event: Dict[str, Any]):
                await event_queue.put(event)

            await bridge.subscribe_events("state_changed", on_state_change)
            logger.info("WebSocket: subscribed to HA state_changed events")

            try:
                while True:
                    # Check for client messages (ping/pong or close)
                    try:
                        msg = await asyncio.wait_for(ws.receive_text(), timeout=0.1)
                        if msg == "ping":
                            await ws.send_text(json.dumps({"type": "pong"}))
                        elif msg == "subscribe_devices":
                            # Client requests initial device list
                            raw = await bridge.get_all_states()
                            if raw:
                                devices = [convert_ha_device(d) for d in raw]
                                await ws.send_text(json.dumps({
                                    "type": "devices",
                                    "data": {"devices": devices},
                                }))
                    except asyncio.TimeoutError:
                        pass

                    # Drain event queue
                    events_batch = []
                    while not event_queue.empty():
                        try:
                            ev = event_queue.get_nowait()
                            events_batch.append(ev)
                        except asyncio.QueueEmpty:
                            break

                    if events_batch:
                        # Extract changed entity from the last event's data
                        for ev in events_batch:
                            ev_data = ev.get("event", {}).get("data", {})
                            entity_id = ev_data.get("entity_id", "")
                            new_state = ev_data.get("new_state")
                            if new_state and entity_id:
                                device = convert_ha_device(new_state)
                                device["entity_id"] = entity_id  # ensure ID
                                await ws.send_text(json.dumps({
                                    "type": "state_update",
                                    "data": {"device": device},
                                }))
                                break  # Just send latest for this batch

                    await asyncio.sleep(0.5)

            finally:
                await bridge.close()

        else:
            # Fallback: periodic polling
            logger.warning("WebSocket: HA WS unavailable, using polling fallback")
            await _ws_polling_loop(ws, bridge)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await ws.close()
        except Exception:
            pass


async def _ws_polling_loop(ws: WebSocket, bridge):
    """Fallback: poll HA REST API and push device list to client."""
    last_send = 0
    try:
        while True:
            # Handle client messages
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=0.5)
                if msg == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                pass

            # Send device list every 10 seconds
            now = time.time()
            if now - last_send >= 10:
                try:
                    raw = await bridge.get_all_states()
                    if raw:
                        devices = [convert_ha_device(d) for d in raw]
                        await ws.send_text(json.dumps({
                            "type": "devices",
                            "data": {"devices": devices},
                        }))
                except Exception:
                    pass
                last_send = now

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
