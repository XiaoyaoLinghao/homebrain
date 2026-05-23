"""
HA Context Builder — converts HA entity states and services into
LLM function-calling context (device status text + function definitions).

Reuses HABridgeClient (P1-T1) for all HA API access.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── domain → human-readable label (Mandarin) ─────────────────────────
_DOMAIN_LABELS: Dict[str, str] = {
    "light": "灯光",
    "switch": "开关",
    "sensor": "传感器",
    "binary_sensor": "传感器",
    "climate": "空调/温控",
    "cover": "窗帘/卷帘",
    "fan": "风扇",
    "lock": "门锁",
    "media_player": "媒体",
    "vacuum": "扫地机",
    "camera": "摄像头",
    "scene": "场景",
    "script": "脚本",
    "automation": "自动化",
    "device_tracker": "设备追踪",
    "person": "人员",
    "zone": "区域",
    "button": "按钮",
    "input_boolean": "虚拟开关",
    "input_number": "虚拟数值",
}

# ── domain → default service mapping for function calling ───────────
_DOMAIN_SERVICES: Dict[str, List[str]] = {
    "light": ["turn_on", "turn_off", "toggle"],
    "switch": ["turn_on", "turn_off", "toggle"],
    "climate": ["set_temperature", "set_hvac_mode", "turn_on", "turn_off"],
    "cover": ["open_cover", "close_cover", "stop_cover"],
    "fan": ["turn_on", "turn_off", "set_speed"],
    "lock": ["lock", "unlock"],
    "media_player": ["media_play", "media_pause", "volume_set"],
    "scene": ["turn_on"],
    "script": ["turn_on"],
    "vacuum": ["start", "stop", "return_to_base"],
}

# ── domain → controllable domain check ─────────────────────────────
_CONTROLLABLE_DOMAINS = frozenset({
    "light", "switch", "climate", "fan", "cover",
})


# ── room extraction helper (reused from web_adapter pattern) ────────
def _room_from_entity_state(entity: Dict[str, Any]) -> str:
    """Extract room from HA entity state dict."""
    attrs = entity.get("attributes", {}) or {}
    entity_id = entity.get("entity_id", "")

    area = attrs.get("area_id") or attrs.get("area") or attrs.get("room")
    if area:
        return area

    _cn_rooms = ["客厅", "卧室", "主卧", "次卧", "厨房", "浴室", "阳台",
                 "书房", "走廊", "餐厅", "玄关", "衣帽间"]

    friendly = attrs.get("friendly_name", "")
    if friendly:
        for cn in _cn_rooms:
            if cn in friendly:
                return cn

    parts = entity_id.split(".")
    if len(parts) >= 2:
        name_parts = parts[1].split("_")
        room_hints = {
            "living": "客厅", "ketin": "客厅",
            "bedroom": "卧室", "woshi": "卧室",
            "主卧": "主卧", "master": "主卧",
            "次卧": "次卧", "second": "次卧",
            "kitchen": "厨房", "chufang": "厨房",
            "bathroom": "浴室", "yushi": "浴室",
            "balcony": "阳台", "yangtai": "阳台",
            "study": "书房", "shufang": "书房",
            "corridor": "走廊", "zoulang": "走廊",
            "dining": "餐厅", "canting": "餐厅",
            "entrance": "玄关", "xuanguan": "玄关",
            "cloakroom": "衣帽间", "yimaojian": "衣帽间",
        }
        for part in name_parts:
            for eng, cn in room_hints.items():
                if eng in part.lower():
                    return cn

    return "其他"
_SERVICE_PARAMS: Dict[str, Dict[str, Any]] = {
    "turn_on": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "目标设备的 entity_id，例如 light.living_room",
            },
        },
        "required": ["entity_id"],
    },
    "turn_off": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "目标设备的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "toggle": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "目标设备的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "set_temperature": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "温控设备的 entity_id",
            },
            "temperature": {
                "type": "number",
                "description": "目标温度（摄氏度）",
            },
        },
        "required": ["entity_id", "temperature"],
    },
    "set_hvac_mode": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "温控设备的 entity_id",
            },
            "hvac_mode": {
                "type": "string",
                "enum": ["off", "heat", "cool", "auto", "dry", "fan_only"],
                "description": "运行模式: off=关, heat=制热, cool=制冷, auto=自动",
            },
        },
        "required": ["entity_id", "hvac_mode"],
    },
    "open_cover": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "窗帘/卷帘的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "close_cover": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "窗帘/卷帘的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "stop_cover": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "窗帘/卷帘的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "set_speed": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "风扇的 entity_id",
            },
            "speed": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "风速: low=低速, medium=中速, high=高速",
            },
        },
        "required": ["entity_id", "speed"],
    },
    "lock": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "门锁的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "unlock": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "门锁的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "media_play": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "媒体播放器的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "media_pause": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "媒体播放器的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "volume_set": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "媒体播放器的 entity_id",
            },
            "volume_level": {
                "type": "number",
                "description": "音量 0.0 ~ 1.0",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["entity_id", "volume_level"],
    },
    "start": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "设备的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "stop": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "设备的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
    "return_to_base": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "扫地机的 entity_id",
            },
        },
        "required": ["entity_id"],
    },
}

# ── service → Chinese descriptions ───────────────────────────────────
_SERVICE_DESCRIPTIONS: Dict[str, str] = {
    "light.turn_on": "打开指定灯光，可设置亮度和颜色",
    "light.turn_off": "关闭指定灯光",
    "light.toggle": "切换灯光开关状态",
    "switch.turn_on": "打开指定开关",
    "switch.turn_off": "关闭指定开关",
    "switch.toggle": "切换开关状态",
    "climate.set_temperature": "设置空调/温控器的目标温度",
    "climate.set_hvac_mode": "设置空调运行模式（制冷/制热/自动等）",
    "climate.turn_on": "打开空调/温控器",
    "climate.turn_off": "关闭空调/温控器",
    "cover.open_cover": "打开窗帘/卷帘",
    "cover.close_cover": "关闭窗帘/卷帘",
    "cover.stop_cover": "停止窗帘/卷帘",
    "fan.turn_on": "打开风扇",
    "fan.turn_off": "关闭风扇",
    "fan.set_speed": "设置风扇风速",
    "lock.lock": "上锁",
    "lock.unlock": "解锁",
    "media_player.media_play": "播放媒体",
    "media_player.media_pause": "暂停媒体",
    "media_player.volume_set": "设置音量",
    "scene.turn_on": "激活场景",
    "script.turn_on": "执行脚本",
    "vacuum.start": "启动扫地机",
    "vacuum.stop": "停止扫地机",
    "vacuum.return_to_base": "扫地机回充",
}


def _sanitize_fn_name(name: str) -> str:
    """Sanitize a string into a valid function name: a-z, 0-9, _."""
    sanitized = re.sub(r"[^a-z0-9_]", "_", name.lower())
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized or "unknown"


def _entity_friendly_name(entity: Dict[str, Any]) -> str:
    """Extract a human-readable name from an HA entity dict."""
    attrs = entity.get("attributes", {})
    friendly_name = attrs.get("friendly_name", "")
    if friendly_name:
        return str(friendly_name)
    # fallback: use entity_id's last segment
    eid = entity.get("entity_id", "")
    return eid.rsplit(".", 1)[-1].replace("_", " ")


def _state_text_short(entity: Dict[str, Any]) -> str:
    """Render one controllable entity as a single compact line.

    Only used for controllable domains (light/switch/climate/fan/cover).
    Example: light.yeelink_xxx (书房吸顶灯): on
    """
    entity_id = entity.get("entity_id", "unknown")
    state = entity.get("state", "unknown")
    friendly = _entity_friendly_name(entity)
    domain = entity_id.split(".", 1)[0] if "." in entity_id else entity_id

    label = _DOMAIN_LABELS.get(domain, domain)
    return f"{entity_id} ({friendly}): {state}"


class HAContextBuilder:
    """Build LLM function-calling context from HA entity states and services.

    Uses HABridgeClient for all data access. Produces:
      - Device status text for the LLM system prompt
      - OpenAI-compatible function definitions from HA service catalog
      - Service execution via HABridgeClient.call_service()
    """

    def __init__(self, ha_client):
        """Provide an HABridgeClient instance.

        Args:
            ha_client: HABridgeClient from ha_bridge module.
        """
        self.ha = ha_client

    # ── device context ───────────────────────────────────────────────

    async def build_device_context(self) -> str:
        """Build a room-grouped device status summary for the LLM.

        Groups all entities by room, provides high-level device counts
        by domain, and only lists individual controllable devices
        (light/switch/climate/fan/cover) grouped by room.

        Output example:
            当前设备状态（共2411个实体，9个区域）：

            📊 设备概览：
            - 灯光: 135个 | 传感器: 1200+ | 开关: 420+ | 空调/温控: 56个 | ...

            区域分布：
            - 主卧: 236个设备 (灯光15, 传感器42, 开关8...)
            - 书房: 165个设备
            ...

            🏠 可控制设备详情（灯光/开关/空调/风扇/窗帘）：
            书房:
              - light.xxx (书房吸顶灯): on
              ...
        """
        states = await self.ha.get_all_states()
        if states is None:
            logger.warning("No entity states available — HA may be unreachable")
            return "（设备状态不可用 — Home Assistant 未连接）"
        if not states:
            return "当前没有可控制的设备。"

        # Filter hidden/internal entities
        visible = [
            e for e in states
            if not e.get("entity_id", "").startswith(
                ("update.", "sun.", "persistent_notification.")
            )
        ]

        if not visible:
            return "当前没有可控制的设备。"

        total = len(visible)

        # Classify: room → list of entities
        room_entities: Dict[str, List[Dict[str, Any]]] = {}
        for entity in visible:
            room = _room_from_entity_state(entity)
            room_entities.setdefault(room, []).append(entity)

        num_rooms = len(room_entities)

        # ── 1) Global domain counts ───────────────────────────────
        domain_counts: Dict[str, int] = {}
        for entity in visible:
            eid = entity.get("entity_id", "")
            domain = eid.split(".", 1)[0] if "." in eid else "other"
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

        overview_lines: List[str] = []
        # Order by most relevant first
        overview_order = ["light", "sensor", "binary_sensor", "switch", "climate",
                          "cover", "fan", "lock", "media_player", "vacuum", "camera",
                          "automation", "script", "scene"]
        seen_domains = set()
        for dom in overview_order:
            cnt = domain_counts.pop(dom, 0)
            if cnt > 0:
                label = _DOMAIN_LABELS.get(dom, dom)
                overview_lines.append(f"- {label}: {cnt}个")
                seen_domains.add(dom)
        # Remaining domains as "其他"
        other_cnt = sum(domain_counts.values())
        if other_cnt > 0:
            overview_lines.append(f"- 其他: {other_cnt}个")

        # ── 2) Per-room device count + domain breakdown ───────────
        # Sort rooms: known rooms first, then by count desc, "其他" last
        _room_order = {
            "客厅": 0, "主卧": 1, "次卧": 2, "书房": 3, "厨房": 4,
            "餐厅": 5, "浴室": 6, "阳台": 7, "走廊": 8, "玄关": 9, "衣帽间": 10,
        }

        def _room_sort_key(item: tuple) -> tuple:
            room, entities = item
            return (_room_order.get(room, 99), -len(entities))

        room_lines: List[str] = []
        for room, entities in sorted(room_entities.items(), key=_room_sort_key):
            room_total = len(entities)
            # Domain breakdown for this room
            rd_counts: Dict[str, int] = {}
            for entity in entities:
                eid = entity.get("entity_id", "")
                domain = eid.split(".", 1)[0] if "." in eid else "other"
                rd_counts[domain] = rd_counts.get(domain, 0) + 1
            breakdown_parts = []
            for dom in overview_order:
                cnt = rd_counts.get(dom, 0)
                if cnt > 0:
                    label = _DOMAIN_LABELS.get(dom, dom)
                    breakdown_parts.append(f"{label}{cnt}")
            breakdown = ", ".join(breakdown_parts) if breakdown_parts else ""
            line = f"- {room}: {room_total}个设备"
            if breakdown:
                line += f" ({breakdown})"
            room_lines.append(line)

        # ── 3) Controllable device details by room ─────────────────
        controllable_lines: List[str] = []
        for room, entities in sorted(room_entities.items(), key=_room_sort_key):
            controllable = [
                e for e in entities
                if e.get("entity_id", "").split(".", 1)[0] in _CONTROLLABLE_DOMAINS
            ]
            if not controllable:
                continue
            controllable_lines.append(f"{room}:")
            displayed = 0
            max_display = 15
            for entity in controllable:
                displayed += 1
                if displayed > max_display:
                    remaining = len(controllable) - max_display
                    controllable_lines.append(f"  ... 余下 {remaining} 个")
                    break
                controllable_lines.append(f"  - {_state_text_short(entity)}")

        # ── Assemble final context ─────────────────────────────────
        parts: List[str] = [f"当前设备状态（共{total}个实体，{num_rooms}个区域）：\n"]

        parts.append("📊 设备概览：")
        parts.extend(overview_lines)
        parts.append("")

        parts.append("区域分布：")
        parts.extend(room_lines)
        parts.append("")

        if controllable_lines:
            parts.append("🏠 可控制设备详情（灯光/开关/空调/风扇/窗帘）：")
            parts.extend(controllable_lines)

        return "\n".join(parts)

    # ── function definitions ─────────────────────────────────────────

    async def build_function_definitions(self) -> List[Dict[str, Any]]:
        """Build OpenAI-compatible function definitions from HA service catalog.

        Tries GET /api/services first for dynamic discovery, then falls
        back to the built-in domain→service mapping.

        Returns a list of dicts, each with:
            {
                "type": "function",
                "function": {
                    "name": "ha_light_turn_on",
                    "description": "打开指定灯光，可设置亮度和颜色",
                    "parameters": { ... JSON Schema ... }
                }
            }
        """
        definitions: List[Dict[str, Any]] = []
        discovered = await self.ha.get_services()

        if discovered and isinstance(discovered, list):
            # Dynamic: iterate HA's real service catalog
            for domain_entry in discovered:
                for svc_name, svc_info in (domain_entry.get("services") or {}).items():
                    fn_name = _sanitize_fn_name(f"ha_{domain_entry.get('domain', '')}_{svc_name}")
                    desc = svc_info.get("description", "") or _SERVICE_DESCRIPTIONS.get(
                        f"{domain_entry.get('domain', '')}.{svc_name}", ""
                    )
                    params = self._build_service_params(domain_entry.get("domain", ""), svc_name)
                    definitions.append({
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "description": desc or f"调用 {domain_entry.get('domain')}.{svc_name} 服务",
                            "parameters": params,
                        },
                    })
        else:
            # Fallback: use built-in domain→service mapping
            for domain, services in _DOMAIN_SERVICES.items():
                for svc in services:
                    fn_name = _sanitize_fn_name(f"ha_{domain}_{svc}")
                    desc = _SERVICE_DESCRIPTIONS.get(f"{domain}.{svc}", f"{domain}.{svc}")
                    params = _SERVICE_PARAMS.get(svc) or _SERVICE_PARAMS.get("turn_on", {})
                    definitions.append({
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "description": desc,
                            "parameters": params,
                        },
                    })

        return definitions

    def _build_service_params(self, domain: str, service: str) -> Dict[str, Any]:
        """Build JSON Schema parameters for a domain.service combination."""
        key = f"{domain}.{service}"
        if domain == "light" and service == "turn_on":
            return {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "灯光的 entity_id"},
                    "brightness": {"type": "integer", "description": "亮度 0-255"},
                    "rgb_color": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "RGB 颜色 [R, G, B]，每个值 0-255",
                    },
                },
                "required": ["entity_id"],
            }
        if domain == "climate" and service == "set_hvac_mode":
            return _SERVICE_PARAMS.get("set_hvac_mode", {})
        if domain == "climate" and service == "set_temperature":
            return _SERVICE_PARAMS.get("set_temperature", {})
        return _SERVICE_PARAMS.get(service) or {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "目标设备的 entity_id"},
            },
            "required": ["entity_id"],
        }

    # ── function call execution ──────────────────────────────────────

    async def execute_function_call(
        self, function_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a function call returned by the LLM.

        Parses the function name into HA domain.service and dispatches
        via HABridgeClient.call_service().

        Args:
            function_name: e.g. "ha_light_turn_on"
            arguments: e.g. {"entity_id": "light.living_room"}

        Returns:
            {"success": True/False, "result": ..., "error": ...}
        """
        # Parse "ha_{domain}_{service}" back to domain.service
        parts = function_name.split("_", 1)  # remove "ha" prefix
        if len(parts) < 2:
            return {"success": False, "error": f"Invalid function name: {function_name}"}

        service_path = parts[1]  # e.g. "light_turn_on"
        # Split on last underscore: domain vs service
        # Try known service names first
        domain = ""
        service = ""
        for d in sorted(_DOMAIN_LABELS.keys(), key=len, reverse=True):
            if service_path.startswith(d + "_"):
                domain = d
                service = service_path[len(d) + 1:]
                break

        if not domain:
            # Fallback: split on first underscore
            idx = service_path.find("_")
            if idx > 0:
                domain = service_path[:idx]
                service = service_path[idx + 1:]

        if not domain or not service:
            return {"success": False, "error": f"Could not parse domain/service from: {function_name}"}

        # Extract entity_id from arguments
        entity_id = arguments.get("entity_id")
        if not entity_id:
            # Try entity_id alternatives
            for key in arguments:
                if "entity" in key.lower():
                    entity_id = arguments[key]
                    break
        if not entity_id:
            return {"success": False, "error": "No entity_id in function arguments"}

        # Build service_data
        service_data: Dict[str, Any] = {"entity_id": entity_id}
        # Forward additional arguments (brightness, temperature, etc.)
        for key, value in arguments.items():
            if key != "entity_id":
                service_data[key] = value

        try:
            result = await self.ha.call_service(domain, service, service_data)
            if result is None:
                return {
                    "success": False,
                    "error": f"Service {domain}.{service} call failed (HA unreachable)",
                }
            return {"success": True, "result": result}
        except Exception as e:
            logger.exception("execute_function_call failed: %s", e)
            return {"success": False, "error": str(e)}
