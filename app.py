"""
HomeBrain v2.0 — FastAPI Application Entry Point

Mounts three module routers:
  - /api/ha/*      HA Bridge (device control)
  - /api/scenes/*  Scene Engine (automation)
  - /api/llm/*     LLM Adapter (natural language control)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ha_bridge import router as ha_router
from scene_engine import router as scene_router
from llm_adapter import router as llm_router
from web_adapter import router as web_router

app = FastAPI(title="HomeBrain v2.0", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(web_router)
app.include_router(ha_router)
app.include_router(scene_router)
app.include_router(llm_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
