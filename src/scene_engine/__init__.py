"""
HomeBrain v2.0 — Scene Engine Module

Event-driven scene/automation engine powered by HATransport (HA REST + WebSocket).
Loads YAML rule files in a declarative trigger→condition→action DSL,
evaluates conditions against live HA state, and executes actions via HA service calls.

Version: 0.1.0
"""

from .engine import SceneEngine
from .router import router

__version__ = "0.1.0"
__all__ = ["SceneEngine", "router"]
