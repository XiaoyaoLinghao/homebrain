"""
LLM Adapter — natural language → device control bridge.

Takes user's Chinese natural language instructions, builds HA device
context + function definitions, sends to LLM (OpenAI-compatible API),
executes any function calls, and returns a natural language reply.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from .ha_context import HAContextBuilder, _entity_friendly_name

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────
_SYSTEM_PROMPT_TEMPLATE = """你是 HomeBrain 智能家居助手，负责通过自然语言控制家庭设备。

## 规则
1. 根据用户意图调用合适的设备控制函数
2. 如果用户只是询问设备状态，直接根据上下文回答，不要调用函数
3. 一次可以调用多个函数（例如"关闭所有灯"）
4. 回复语言：中文，简洁友好
5. 如果用户指令不明确，主动询问澄清

## 当前设备上下文
{device_context}"""


# ── mock LLM client for testing ──────────────────────────────────────
class MockLLMClient:
    """Predictable mock for tests — returns pre-configured responses."""

    def __init__(self, function_call_result: Optional[Dict[str, Any]] = None):
        self._fn_result = function_call_result
        self.calls: List[Dict[str, Any]] = []

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self.calls.append({"messages": messages, "tools": tools})

        if self._fn_result:
            return {
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": self._fn_result,
                    },
                }],
            }
        # Default: plain text reply
        return {
            "choices": [{
                "message": {
                    "content": "好的，已完成。",
                },
            }],
        }


# ── deepseek client ──────────────────────────────────────────────────
class DeepSeekClient:
    """Lightweight OpenAI-compatible client for DeepSeek API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        import aiohttp

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()


# ── main adapter ─────────────────────────────────────────────────────
class LLMAdapter:
    """Natural language → device control adapter.

    Flow:
        1. Build device context + function definitions from HA
        2. Send user message + context to LLM
        3. If LLM returns tool_calls → execute via HA → build result summary
        4. Return structured result with reply text

    Usage:
        adapter = LLMAdapter(ha_client)
        result = await adapter.process("打开客厅灯")
        # result["reply"] → "已打开客厅灯"
    """

    def __init__(self, ha_client, llm_client=None):
        """Initialize with an HABridgeClient and optional LLM client.

        Args:
            ha_client: HABridgeClient instance
            llm_client: An object with an async chat(messages, tools) method.
                        If None, defaults to DeepSeekClient.
        """
        self.ha = ha_client
        self.context_builder = HAContextBuilder(ha_client)
        self.llm = llm_client or DeepSeekClient()

    async def process(self, user_message: str) -> Dict[str, Any]:
        """Process a user message and return structured result.

        Args:
            user_message: Natural language instruction in Chinese

        Returns:
            {
                "reply": str,              # human-readable response
                "tool_calls": list | None,  # function calls executed (or None)
                "call_results": list,       # per-call success/failure
                "context_used": str,        # device context sent to LLM
            }
        """
        if not user_message or not user_message.strip():
            return {"reply": "请问需要我帮您控制什么设备？", "tool_calls": None, "call_results": [], "context_used": ""}

        # Step 1: Build context
        device_context = await self.context_builder.build_device_context()
        function_defs = await self.context_builder.build_function_definitions()

        # Step 2: Construct LLM messages
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(device_context=device_context)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message.strip()},
        ]

        # Step 3: Call LLM
        try:
            llm_response = await self.llm.chat(messages, tools=function_defs if function_defs else None)
        except Exception as e:
            logger.exception("LLM call failed")
            return {
                "reply": f"抱歉，智能服务暂时不可用（{e}）。请稍后重试。",
                "tool_calls": None,
                "call_results": [],
                "context_used": device_context,
            }

        # Step 4: Parse LLM response
        choice = (llm_response.get("choices") or [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")

        # Plain text reply — no function calls needed
        if not tool_calls:
            reply = content or "好的，已了解。"
            return {
                "reply": reply,
                "tool_calls": None,
                "call_results": [],
                "context_used": device_context,
            }

        # Step 5: Execute function calls
        call_results: List[Dict[str, Any]] = []
        execution_summaries: List[str] = []

        for tc in tool_calls:
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            try:
                fn_args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                call_results.append({
                    "function_name": fn_name,
                    "success": False,
                    "error": "Invalid JSON arguments",
                })
                continue

            result = await self.context_builder.execute_function_call(fn_name, fn_args)
            call_results.append({
                "function_name": fn_name,
                "arguments": fn_args,
                **result,
            })

            # Build human-readable execution summary
            entity_id = fn_args.get("entity_id", "unknown")
            if result.get("success"):
                friendly = await self._get_friendly_name(entity_id)
                summary = f"✅ {friendly}: {self._describe_action(fn_name, fn_args)}"
            else:
                summary = f"❌ {fn_name}: {result.get('error', '执行失败')}"
            execution_summaries.append(summary)

        # Step 6: Build final reply
        if content:
            reply = content
        else:
            reply = "执行结果：\n" + "\n".join(execution_summaries)

        return {
            "reply": reply,
            "tool_calls": [{
                "name": tc.get("function", {}).get("name"),
                "arguments": json.loads(tc.get("function", {}).get("arguments", "{}")),
            } for tc in tool_calls],
            "call_results": call_results,
            "context_used": device_context,
        }

    async def _get_friendly_name(self, entity_id: str) -> str:
        """Resolve entity_id to friendly name via HA state lookup."""
        if not entity_id:
            return "未知设备"
        try:
            state = await self.ha.get_entity_state(entity_id)
            if state:
                return _entity_friendly_name(state)
        except Exception:
            pass
        return entity_id

    @staticmethod
    def _describe_action(function_name: str, arguments: Dict[str, Any]) -> str:
        """Return a short Chinese description of the performed action."""
        name_lower = function_name.lower()
        if "turn_on" in name_lower or "open" in name_lower or "unlock" in name_lower:
            return "已开启"
        if "turn_off" in name_lower or "close" in name_lower or "lock" in name_lower:
            return "已关闭"
        if "toggle" in name_lower:
            return "已切换"
        if "set_temperature" in name_lower:
            temp = arguments.get("temperature", "?")
            return f"温度已设为 {temp}°C"
        if "set_hvac_mode" in name_lower:
            mode = arguments.get("hvac_mode", "?")
            return f"模式已设为 {mode}"
        if "set_speed" in name_lower:
            speed = arguments.get("speed", "?")
            return f"风速已设为 {speed}"
        if "volume_set" in name_lower:
            vol = arguments.get("volume_level", "?")
            return f"音量已设为 {vol}"
        if "return_to_base" in name_lower:
            return "已返回充电座"
        if "start" in name_lower:
            return "已启动"
        if "stop" in name_lower:
            return "已停止"
        return "已执行"
