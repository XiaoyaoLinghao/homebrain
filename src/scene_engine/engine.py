"""
Scene Engine — YAML-driven declarative automation engine.

Loads scene rule files in trigger → condition → action DSL format.
Evaluates triggers and conditions against live HA device state via HATransport
and executes HA service calls for matched rules.

Lifecycle::

    engine = SceneEngine(transport, rules_dir="/path/to/rules/")
    await engine.load_rules()
    await engine.start()
    # ... wait for scene triggers ...
    await engine.stop()
"""

import asyncio
import logging
import operator as op
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .ha_transport import HATransport

logger = logging.getLogger(__name__)

# ── Operators available in condition expressions ──────────────────

_OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "eq": op.eq,
    "ne": op.ne,
    "gt": op.gt,
    "gte": op.ge,
    "lt": op.lt,
    "lte": op.le,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "between": lambda a, b: b[0] <= a <= b[1] if isinstance(b, (list, tuple)) and len(b) == 2 else False,
}

_STATE_ATTRIBUTE_KEYS = {"entity_id", "state", "attributes", "last_changed", "last_updated"}


# ── Condition evaluation helpers ──────────────────────────────────

def _extract_value(state: Dict[str, Any], attr_name: Optional[str]) -> Any:
    """Extract a comparable value from an HA entity state dict."""
    if attr_name is None or attr_name == "state":
        return state.get("state")
    if attr_name in _STATE_ATTRIBUTE_KEYS:
        return state.get(attr_name)
    # nested attribute: e.g. attributes.brightness, attributes.supported_features
    attrs = state.get("attributes", {})
    return attrs.get(attr_name)


def _cast_value(raw: Any, target: Any) -> Any:
    """Coerce raw (string from HA) to the type of target for comparison."""
    if raw is None or target is None:
        return raw
    if isinstance(target, bool):
        if isinstance(raw, str):
            return raw.lower() in ("true", "on", "1", "yes", "open", "unlocked")
        return bool(raw)
    if isinstance(target, (int, float)):
        try:
            return float(raw) if isinstance(target, float) else int(float(raw))
        except (ValueError, TypeError):
            return raw
    if isinstance(target, list):
        # For 'between' / 'in' operators: coerce raw to match list element type
        if len(target) > 0 and isinstance(target[0], (int, float)):
            try:
                return float(raw) if isinstance(target[0], float) else int(float(raw))
            except (ValueError, TypeError):
                return raw
        return raw
    return raw


def _evaluate_condition(condition: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Evaluate a single condition against a device state."""
    attr = condition.get("attribute", condition.get("field"))
    cmp_op = condition.get("operator", "eq")
    expected = condition.get("value")

    actual = _extract_value(state, attr)
    actual = _cast_value(actual, expected)

    fn = _OPERATORS.get(cmp_op)
    if fn is None:
        logger.warning("Unknown operator '%s' — treating as failed", cmp_op)
        return False

    try:
        return fn(actual, expected)
    except Exception:
        logger.exception("Condition evaluation error: %s %s %s", actual, cmp_op, expected)
        return False


# ── Scene Engine ──────────────────────────────────────────────────

class SceneEngine:
    """YAML-driven automation engine.

    Rules are loaded from .yaml files in *rules_dir*. Each file defines one
    or more scenes under a ``scenes`` key.  Scenes follow the DSL:

        scenes:
          - name: "My Scene"
            enabled: true
            trigger:
              entity_id: "binary_sensor.door"
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

    All public methods are async-safe and catch internal exceptions.
    """

    def __init__(
        self,
        transport: HATransport,
        rules_dir: Optional[str] = None,
        poll_interval: float = 5.0,
    ):
        self._transport = transport
        self._rules_dir = Path(rules_dir or os.environ.get("SCENE_RULES_DIR", "rules"))
        self._poll_interval = poll_interval
        self._scenes: List[Dict[str, Any]] = []
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._scene_state: Dict[str, Dict[str, Any]] = {}  # scene_name → {"active": bool, …}

    # ── Rule loading ─────────────────────────────────────────────

    async def load_rules(self, rules_dir: Optional[str] = None) -> int:
        """Load YAML scene rule files from *rules_dir*.

        Returns the number of loaded scenes (enabled + disabled).
        """
        target = Path(rules_dir) if rules_dir else self._rules_dir
        if not target.exists():
            logger.warning("Rules directory not found: %s", target)
            return 0

        loaded: List[Dict[str, Any]] = []
        for yf in sorted(target.glob("**/*.yaml")) + sorted(target.glob("**/*.yml")):
            try:
                raw = yaml.safe_load(yf.read_text(encoding="utf-8"))
                if not raw:
                    continue
                scenes = raw if isinstance(raw, list) else raw.get("scenes", [raw])
                if isinstance(scenes, list):
                    for scene in scenes:
                        if isinstance(scene, dict):
                            scene["_source_file"] = str(yf)
                            loaded.append(scene)
                logger.debug("Loaded %d scene(s) from %s", len(scenes) if isinstance(scenes, list) else 1, yf.name)
            except yaml.YAMLError as e:
                logger.error("Failed to parse %s: %s", yf, e)
            except Exception:
                logger.exception("Unexpected error loading %s", yf)

        # Validate and index
        validated: List[Dict[str, Any]] = []
        for scene in loaded:
            errors = _validate_scene_structure(scene)
            if errors:
                logger.error("Scene '%s' (from %s) has errors: %s — skipped",
                             scene.get("name", "unnamed"), scene.get("_source_file"), errors)
                continue
            validated.append(scene)

        self._scenes = validated
        # Init scene state
        for scene in self._scenes:
            name = scene.get("name", scene.get("_source_file", "unnamed"))
            self._scene_state[name] = {"active": False, "last_triggered": None}

        logger.info("Loaded %d scenes (%d enabled) from %s",
                    len(validated), sum(1 for s in validated if s.get("enabled", True)), target)
        return len(validated)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> bool:
        """Start the engine: connect HA transport and begin evaluation loop.

        Subscribes to WebSocket state_changed events for all trigger entity_ids
        when WS is available, then runs periodic polling as a fallback.

        Returns True if the engine started successfully.
        """
        ws_ok = await self._transport.start()

        # Collect all trigger entity_ids for subscriptions
        trigger_entities: List[str] = []
        for scene in self._scenes:
            if not scene.get("enabled", True):
                continue
            trigger = scene.get("trigger", {})
            eid = trigger.get("entity_id")
            if eid and eid not in trigger_entities:
                trigger_entities.append(eid)
            # Also collect condition entity_ids
            for cond in scene.get("conditions", []):
                ceid = cond.get("entity_id")
                if ceid and ceid not in trigger_entities:
                    trigger_entities.append(ceid)

        # Subscribe to device changes (non-blocking; errors logged internally)
        if ws_ok and trigger_entities:

            async def on_state_change(event: Dict[str, Any]):
                await self._on_device_state_changed(event)

            await self._transport.subscribe_device_changes(trigger_entities, on_state_change)

        # Start periodic evaluation loop
        self._running = True
        self._poll_task = asyncio.create_task(self._evaluation_loop())

        logger.info("SceneEngine started (poll=%.1fs, ws=%s, scenes=%d, trigger_entities=%d)",
                     self._poll_interval, ws_ok, len(self._scenes), len(trigger_entities))
        return True

    async def stop(self):
        """Gracefully stop the engine."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._transport.close()
        logger.info("SceneEngine stopped")

    # ── Evaluation loop ──────────────────────────────────────────

    async def _evaluation_loop(self):
        """Periodically evaluate all enabled scenes."""
        while self._running:
            try:
                await self._evaluate_all_scenes()
            except Exception:
                logger.exception("Scene evaluation cycle failed")
            await asyncio.sleep(self._poll_interval)

    async def _evaluate_all_scenes(self):
        """Evaluate each enabled scene: trigger → conditions → actions."""
        for scene in self._scenes:
            if not scene.get("enabled", True):
                continue
            try:
                await self._evaluate_scene(scene)
            except Exception:
                logger.exception("Failed to evaluate scene '%s'", scene.get("name", "unnamed"))

    async def _evaluate_scene(self, scene: Dict[str, Any]):
        """Evaluate a single scene rule."""
        name = scene.get("name", "unnamed")

        # 1. Check trigger
        trigger = scene.get("trigger", {})
        trigger_entity = trigger.get("entity_id")
        if not trigger_entity:
            # Manual-only scenes (no entity_id) skip automatic evaluation
            return

        state = await self._transport.get_device_state(trigger_entity)
        if state is None:
            return

        current = state.get("state")
        trigger_from = trigger.get("from")
        trigger_to = trigger.get("to")

        # If trigger defines from→to, check state transition
        changed = True
        if trigger_from is not None and trigger_to is not None:
            changed = str(trigger_from) != str(trigger_to)
            if str(current) != trigger_to:
                return  # trigger not met

        # 2. Evaluate conditions
        conditions = scene.get("conditions", [])
        if conditions and not await self._evaluate_conditions(conditions):
            return

        # 3. Debounce: don't re-trigger too fast
        scene_state = self._scene_state.get(name, {})
        now = datetime.now(timezone.utc)
        last = scene_state.get("last_triggered")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() < self._poll_interval:
                    return
            except (ValueError, TypeError):
                pass

        # 4. Execute actions
        logger.info("Scene triggered: %s (entity=%s state=%s)", name, trigger_entity, current)
        self._scene_state[name] = {"active": True, "last_triggered": now.isoformat()}

        for action in scene.get("actions", []):
            await self._execute_action(action)

    async def _evaluate_conditions(self, conditions: List[Dict[str, Any]]) -> bool:
        """Evaluate all conditions (AND logic)."""
        # Group conditions by entity_id for batch fetch
        entity_ids: List[str] = []
        seen: set = set()
        for cond in conditions:
            eid = cond.get("entity_id")
            if eid and eid not in seen:
                entity_ids.append(eid)
                seen.add(eid)

        states = await self._transport.get_device_states(entity_ids) if entity_ids else {}

        for cond in conditions:
            eid = cond.get("entity_id")
            state = states.get(eid)
            if state is None:
                # Try fetching individually
                state = await self._transport.get_device_state(eid)
            if state is None:
                logger.debug("Condition entity '%s' unavailable — condition fails", eid)
                return False
            if not _evaluate_condition(cond, state):
                return False

        return True

    async def _execute_action(self, action: Dict[str, Any]):
        """Execute a single action via HA service call."""
        domain = action.get("domain", "")
        service = action.get("service", "")
        target = action.get("target", {})
        data = action.get("data", {})

        # Merge target into data as HA expects entity_id at top level
        call_data: Dict[str, Any] = dict(data)
        if target:
            call_data.update(target)

        logger.debug("Executing: %s.%s → %s", domain, service, call_data)
        result = await self._transport.execute_action(domain, service, call_data)
        if result is None:
            logger.warning("Action failed: %s.%s → %s", domain, service, call_data)

    # ── WebSocket event handler ──────────────────────────────────

    async def _on_device_state_changed(self, event: Dict[str, Any]):
        """Handle state_changed WS event — trigger immediate evaluation."""
        data = event.get("event", {}).get("data", {})
        old = data.get("old_state", {})
        new = data.get("new_state", {})
        entity_id = data.get("entity_id", "")

        for scene in self._scenes:
            if not scene.get("enabled", True):
                continue
            trigger = scene.get("trigger", {})
            if trigger.get("entity_id") != entity_id:
                continue

            old_state = old.get("state") if old else None
            new_state_str = new.get("state") if new else None
            trigger_from = trigger.get("from")
            trigger_to = trigger.get("to")

            # Check if this state change matches the trigger transition
            if trigger_from is not None and trigger_to is not None:
                if new_state_str != trigger_to:
                    continue
            elif trigger_from is not None:
                if old_state == trigger_from and new_state_str != trigger_from:
                    pass
                else:
                    continue

            # Re-evaluate conditions inline
            conditions = scene.get("conditions", [])
            if conditions and not await self._evaluate_conditions(conditions):
                continue

            name = scene.get("name", "unnamed")
            logger.info("WebSocket-triggered scene: %s (%s → %s)", name, old_state, new_state_str)
            self._scene_state[name] = {"active": True, "last_triggered": datetime.now(timezone.utc).isoformat()}

            for action in scene.get("actions", []):
                await self._execute_action(action)

    # ── Public API ───────────────────────────────────────────────

    def list_scenes(self) -> List[Dict[str, Any]]:
        """Return all loaded scenes (without internal fields)."""
        result = []
        for s in self._scenes:
            d = {k: v for k, v in s.items() if not k.startswith("_")}
            name = d.get("name", "unnamed")
            state = self._scene_state.get(name, {})
            d["_state"] = state
            result.append(d)
        return result

    async def trigger_scene_manually(self, scene_name: str) -> bool:
        """Manually execute a named scene (bypassing trigger/conditions).

        Returns True if the scene was found and actions executed.
        """
        for scene in self._scenes:
            if scene.get("name") == scene_name:
                logger.info("Manual trigger: %s", scene_name)
                for action in scene.get("actions", []):
                    await self._execute_action(action)
                self._scene_state[scene_name] = {
                    "active": True,
                    "last_triggered": datetime.now(timezone.utc).isoformat(),
                }
                return True
        logger.warning("Scene not found for manual trigger: %s", scene_name)
        return False

    def scene_count(self) -> int:
        """Return the number of loaded scenes."""
        return len(self._scenes)

    @property
    def transport(self) -> HATransport:
        """Expose the underlying HATransport for direct use."""
        return self._transport


# ── Validation ────────────────────────────────────────────────────

def _validate_scene_structure(scene: Dict[str, Any]) -> List[str]:
    """Validate a scene dict and return a list of error messages."""
    errors: List[str] = []

    if not isinstance(scene.get("name"), str):
        errors.append("missing or invalid 'name'")
    if not isinstance(scene.get("trigger"), dict):
        errors.append("missing or invalid 'trigger'")
    else:
        trigger = scene["trigger"]
        eid = trigger.get("entity_id")
        if eid is not None and not isinstance(eid, str):
            errors.append("trigger.entity_id must be a string")

    actions = scene.get("actions", [])
    if not isinstance(actions, list):
        errors.append("'actions' must be a list")
    else:
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                errors.append(f"action[{i}] must be a dict")
                continue
            if not action.get("domain"):
                errors.append(f"action[{i}] missing 'domain'")
            if not action.get("service"):
                errors.append(f"action[{i}] missing 'service'")

    conditions = scene.get("conditions", [])
    if not isinstance(conditions, list):
        errors.append("'conditions' must be a list")

    return errors
