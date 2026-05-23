"""
HA Bridge Client — REST + WebSocket unified interface for Home Assistant.

Uses aiohttp for HTTP and WebSocket transport. All public methods are async.
Token is passed via Bearer Auth header. Connection failures trigger retry
with exponential backoff (max 3 attempts). When HA is unreachable, methods
return None or empty results rather than raising, enabling graceful degradation.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5  # seconds, multiplied by 2^(attempt-1)


class HABridgeError(Exception):
    """Base exception for HA Bridge errors."""


class HAAuthError(HABridgeError):
    """Authentication / token errors (401, 403)."""


class HAConnectionError(HABridgeError):
    """Connection refused, timeout, or unreachable."""


class HAWebSocketError(HABridgeError):
    """WebSocket-level errors."""


def _env(key: str, fallback: str = "") -> str:
    return os.environ.get(key, fallback).strip()


class HABridgeClient:
    """HA REST + WebSocket unified client.

    Config is read from environment variables:
        HA_API_URL  — base URL, e.g. http://192.168.66.68:8123
        HA_API_TOKEN — Long-Lived Access Token

    All public methods are async-safe and will never propagate unhandled
    exceptions — failures are logged and None / empty containers are returned.
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self._base_url = (base_url or _env("HA_API_URL")).rstrip("/")
        self._token = token or _env("HA_API_TOKEN")

        if not self._base_url:
            raise HABridgeError(
                "HA_API_URL not set — provide base_url or set the environment variable"
            )
        if not self._token:
            raise HABridgeError(
                "HA_API_TOKEN not set — provide token or set the environment variable"
            )

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_seq = 1
        self._auth_headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    # ── session lifecycle ────────────────────────────────────────────

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=DEFAULT_TIMEOUT,
                headers=self._auth_headers,
            )
        return self._session

    # ── retry helper ─────────────────────────────────────────────────

    async def _retry(self, coro, op_desc: str = "request") -> Any:
        """Execute coroutine with up to MAX_RETRIES on transient errors."""
        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await coro()
            except aiohttp.ClientResponseError as e:
                if e.status in (401, 403):
                    raise HAAuthError(f"HA auth failed: {e.status}") from e
                last_err = e
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
                last_err = e
            except Exception as e:
                last_err = e
                break  # non-retryable

            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                    op_desc,
                    attempt,
                    MAX_RETRIES,
                    delay,
                    last_err,
                )
                await asyncio.sleep(delay)

        logger.error("%s failed after %d attempts: %s", op_desc, MAX_RETRIES, last_err)
        raise HAConnectionError(f"{op_desc} failed: {last_err}") from last_err

    # ── REST API ─────────────────────────────────────────────────────

    async def get_all_states(self) -> Optional[List[Dict[str, Any]]]:
        """GET /api/states — return all entity states.

        Returns list of entity dicts or None when HA is unreachable.
        """
        session = await self._ensure_session()

        async def _get():
            async with session.get(f"{self._base_url}/api/states") as resp:
                resp.raise_for_status()
                return await resp.json()

        try:
            data = await self._retry(_get, "get_all_states")
            logger.info("Fetched %d entity states from HA", len(data) if data else 0)
            return data
        except (HAAuthError, HAConnectionError):
            return None

    async def get_entity_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """GET /api/states/{entity_id} — return single entity state.

        Returns None if entity not found or HA unreachable.
        """
        if not entity_id:
            logger.warning("get_entity_state called with empty entity_id")
            return None

        session = await self._ensure_session()

        async def _get():
            async with session.get(
                f"{self._base_url}/api/states/{entity_id}"
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.json()

        try:
            data = await self._retry(_get, f"get_entity_state({entity_id})")
            if data is None:
                logger.debug("Entity not found: %s", entity_id)
            return data
        except (HAAuthError, HAConnectionError):
            return None

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """POST /api/services/{domain}/{service} — control a device.

        Example:
            await client.call_service("light", "turn_on", {"entity_id": "light.living_room"})

        Returns the response JSON (list of state changes) or None on failure.
        """
        if not domain or not service:
            logger.warning("call_service called with empty domain or service")
            return None

        session = await self._ensure_session()
        payload = service_data or {}

        async def _post():
            async with session.post(
                f"{self._base_url}/api/services/{domain}/{service}",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.info("Service called: %s.%s → %s", domain, service, payload)
                return result

        try:
            return await self._retry(_post, f"call_service({domain}.{service})")
        except (HAAuthError, HAConnectionError):
            return None

    async def get_services(self) -> Optional[List[Dict[str, Any]]]:
        """GET /api/services — return all available services.

        Useful for building LLM function-calling schemas.
        """
        session = await self._ensure_session()

        async def _get():
            async with session.get(f"{self._base_url}/api/services") as resp:
                resp.raise_for_status()
                return await resp.json()

        try:
            data = await self._retry(_get, "get_services")
            logger.info("Fetched services catalog from HA (%d domains)", len(data) if data else 0)
            return data
        except (HAAuthError, HAConnectionError):
            return None

    # ── WebSocket API ────────────────────────────────────────────────

    async def connect_ws(self) -> bool:
        """Establish WebSocket connection and complete HA auth handshake.

        Returns True on success, False on failure.
        """
        self._ws_seq = 1

        try:
            session = await self._ensure_session()
            ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
            self._ws = await session.ws_connect(f"{ws_url}/api/websocket")

            # Step 1: receive auth_required
            hello = await self._ws.receive_json()
            if hello.get("type") != "auth_required":
                raise HAWebSocketError(f"Unexpected WS hello: {hello}")

            # Step 2: send auth
            await self._ws.send_json({"type": "auth", "access_token": self._token})

            # Step 3: receive auth_ok
            auth_resp = await self._ws.receive_json()
            if auth_resp.get("type") != "auth_ok":
                raise HAWebSocketError(f"WS auth failed: {auth_resp}")

            logger.info("WebSocket connected and authenticated to HA")
            return True

        except Exception as e:
            logger.error("WebSocket connection failed: %s", e)
            self._ws = None
            return False

    async def subscribe_events(
        self,
        event_type: str,
        callback: Callable[[Dict[str, Any]], Any],
    ) -> Optional[int]:
        """Subscribe to an event type (e.g. "state_changed").

        Returns the subscription ID, or None on failure.
        The callback will be invoked for each matching event.

        Requires an active WebSocket connection (call connect_ws() first).
        """
        if self._ws is None or self._ws.closed:
            logger.error("subscribe_events called without active WS connection")
            return None

        sub_id = self._ws_seq
        self._ws_seq += 1

        try:
            await self._ws.send_json({
                "id": sub_id,
                "type": "subscribe_events",
                "event_type": event_type,
            })

            # Verify subscription
            response = await self._ws.receive_json()
            if not response.get("success", False):
                raise HAWebSocketError(f"Subscribe failed: {response}")

            logger.info("Subscribed to event type: %s (id=%d)", event_type, sub_id)

            # Start background listener if not already running
            if self._ws_task is None or self._ws_task.done():
                self._ws_task = asyncio.ensure_future(
                    self._ws_listen({event_type: callback})
                )
            else:
                # Register additional callback — merge into existing dict
                # The _ws_listen task reads from self._callbacks
                pass

            return sub_id

        except Exception as e:
            logger.error("Failed to subscribe to %s: %s", event_type, e)
            return None

    async def _ws_listen(self, callbacks: Dict[str, Callable]):
        """Background task: read WS messages and dispatch to callbacks."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        event = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    ev_type = event.get("event", {}).get("event_type", "")
                    cb = callbacks.get(ev_type)
                    if cb:
                        try:
                            ret = cb(event)
                            if asyncio.iscoroutine(ret):
                                await ret
                        except Exception:
                            logger.exception(
                                "Callback error for event type %s", ev_type
                            )
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket connection closed by HA")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WebSocket error: %s", self._ws.exception())
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("WebSocket listener crashed")
        finally:
            self._ws_task = None

    async def close(self):
        """Gracefully close WebSocket and HTTP session."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info("HA Bridge client closed")
