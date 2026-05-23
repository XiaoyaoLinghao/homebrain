"""
HA Bridge Router — FastAPI endpoints for Home Assistant device control.

GET  /api/ha/devices         — list all device states
GET  /api/ha/devices/{id}    — get single device state
POST /api/ha/services/{domain}/{service} — call HA service
GET  /api/ha/health          — HA connection health check
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .client import HABridgeClient, HABridgeError, HAAuthError, HAConnectionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ha", tags=["ha"])

_bridge: Optional[HABridgeClient] = None


def get_bridge() -> HABridgeClient:
    global _bridge
    if _bridge is None:
        _bridge = HABridgeClient()
    return _bridge


# ── models ──────────────────────────────────────────────────────────

class DeviceState(BaseModel):
    entity_id: str
    state: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    last_changed: Optional[str] = None
    last_updated: Optional[str] = None


class ServiceRequest(BaseModel):
    service_data: Dict[str, Any] = Field(default_factory=dict, description="HA service data payload")


class ServiceResponse(BaseModel):
    success: bool
    domain: str
    service: str
    result: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    ha_connected: bool
    ha_url: str


# ── endpoints ───────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    bridge = get_bridge()
    try:
        ok = await bridge.ping()
        return {"status": "ok", "ha_connected": ok, "ha_url": bridge._base_url}
    except Exception:
        return {"status": "degraded", "ha_connected": False, "ha_url": bridge._base_url}


@router.get("/devices", response_model=List[DeviceState])
async def list_devices():
    try:
        bridge = get_bridge()
        states = await bridge.get_states()
        if states is None:
            return []
        return [DeviceState(**s) for s in states]
    except HAConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HAAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HABridgeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/devices/{entity_id}", response_model=DeviceState)
async def get_device(entity_id: str):
    try:
        bridge = get_bridge()
        state = await bridge.get_state(entity_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Device '{entity_id}' not found")
        return DeviceState(**state)
    except HAConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HAAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HABridgeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/services/{domain}/{service}", response_model=ServiceResponse)
async def call_service(domain: str, service: str, body: ServiceRequest):
    try:
        bridge = get_bridge()
        result = await bridge.call_service(domain, service, body.service_data)
        return ServiceResponse(success=True, domain=domain, service=service, result=result)
    except HAConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HAAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HABridgeError as e:
        raise HTTPException(status_code=500, detail=str(e))
