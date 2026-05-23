"""
Scene Engine Router — FastAPI endpoints for scene/automation management.

GET  /api/scenes              — list available scenes
GET  /api/scenes/{name}        — get scene definition
POST /api/scenes/{name}/run   — manually trigger a scene
GET  /api/scenes/health       — scene engine status
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .engine import SceneEngine
from .ha_transport import HATransport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scenes", tags=["scenes"])

_engine: Optional[SceneEngine] = None


def get_engine() -> SceneEngine:
    global _engine
    if _engine is None:
        from ha_bridge.client import HABridgeClient

        ha = HABridgeClient()
        transport = HATransport(ha)
        _engine = SceneEngine(transport)
    return _engine


# ── models ──────────────────────────────────────────────────────────

class SceneInfo(BaseModel):
    name: str
    description: str = ""
    triggers: List[Dict[str, Any]] = Field(default_factory=list)
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)


class SceneListResponse(BaseModel):
    scenes: List[SceneInfo]
    count: int


class SceneRunResponse(BaseModel):
    success: bool
    scene: str
    actions_executed: int = 0
    errors: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
    scenes_loaded: int
    running: bool


# ── endpoints ───────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    engine = get_engine()
    return {
        "status": "ok",
        "version": "0.1.0",
        "scenes_loaded": len(engine._scenes),
        "running": engine._running,
    }


@router.get("", response_model=SceneListResponse)
async def list_scenes():
    engine = get_engine()
    scenes = [
        SceneInfo(
            name=s["name"],
            description=s.get("description", ""),
            triggers=s.get("triggers", []),
            conditions=s.get("conditions", []),
            actions=s.get("actions", []),
        )
        for s in engine._scenes
    ]
    return SceneListResponse(scenes=scenes, count=len(scenes))


@router.get("/{name}", response_model=SceneInfo)
async def get_scene(name: str):
    engine = get_engine()
    for s in engine._scenes:
        if s["name"] == name:
            return SceneInfo(
                name=s["name"],
                description=s.get("description", ""),
                triggers=s.get("triggers", []),
                conditions=s.get("conditions", []),
                actions=s.get("actions", []),
            )
    raise HTTPException(status_code=404, detail=f"Scene '{name}' not found")


@router.post("/{name}/run", response_model=SceneRunResponse)
async def run_scene(name: str):
    engine = get_engine()
    scene = None
    for s in engine._scenes:
        if s["name"] == name:
            scene = s
            break
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene '{name}' not found")

    try:
        results = await engine._execute_actions(scene.get("actions", []))
        errors = [r.get("error") for r in results if r and not r.get("success") and r.get("error")]
        return SceneRunResponse(
            success=len(errors) == 0,
            scene=name,
            actions_executed=len(results),
            errors=errors,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
