"""大模型客户端（OpenAI 兼容协议）。

为什么不用 LangChain：本项目的工具数量少、编排逻辑需要完全透明（写操作要
两段式确认、每次调用都要审计），直接使用官方 SDK 的函数调用协议代码量更小、
依赖更轻（只多一个 `openai` 包）。参考项目里 LangChain 的价值主要在
多工具 ReAct 编排，这一层我们用 80 行显式循环实现了等价能力。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings


class ChatUnavailable(RuntimeError):
    """没有配置 Key 或未安装 SDK —— 上层应降级到规则模式。"""


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class ChatClient:
    """极薄的封装：只暴露 complete()，便于测试时替换成假客户端。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.settings.chat_ready)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.settings.chat_ready:
            raise ChatUnavailable("未配置 DEEPSEEK_API_KEY，AI 对话能力降级为规则模式")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ChatUnavailable(
                "未安装 openai SDK，请执行 pip install -r requirements-ai.txt"
            ) from exc
        self._client = OpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.chat_api_base,
            timeout=self.settings.chat_timeout,
            max_retries=self.settings.chat_max_retries,
        )
        return self._client

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> ChatReply:
        client = self._ensure_client()
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001 - 统一转成可降级异常
            raise ChatUnavailable(f"调用大模型失败：{exc}") from exc

        choice = response.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(call_id=call.id, name=call.function.name, arguments=arguments))

        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatReply(
            content=(message.content or "").strip(),
            tool_calls=calls,
            finish_reason=choice.finish_reason or "",
            usage=usage,
        )

    def complete_json(self, prompt: str) -> dict[str, Any]:
        """单轮 JSON 抽取（记账解析、商品信息解析）。失败返回空字典。"""
        messages = [
            {"role": "system", "content": "你只输出合法 JSON，不要输出任何解释或 Markdown 代码块。"},
            {"role": "user", "content": prompt},
        ]
        try:
            reply = self.complete(messages)
        except ChatUnavailable:
            return {}
        text = reply.content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if "\n" in text else text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


__all__ = ["ChatClient", "ChatReply", "ChatUnavailable", "ToolCall"]
