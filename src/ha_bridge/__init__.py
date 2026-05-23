"""
HomeBrain v2.0 — HA Bridge Module

Unified REST + WebSocket client for Home Assistant integration.
Provides device state retrieval, service calls, and event subscription
for HomeBrain's Scene Engine and LLM Adapter.

Version: 0.1.0
"""

from .client import HABridgeClient
from .router import router

__version__ = "0.1.0"
__all__ = ["HABridgeClient", "router"]
