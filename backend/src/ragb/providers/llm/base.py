"""One streaming chat interface over four public backends.

Every provider yields the same event stream, so the chat runner never branches on which
model is answering:

    {"type": "text",     "text": str}
    {"type": "thinking", "text": str}
    {"type": "tool_call","id": str, "name": str, "input": dict}
    {"type": "tool_result", "id": str, "name": str, "output": str, "is_error": bool}
    {"type": "usage",    "input_tokens": int, "output_tokens": int}

``native_loop`` is the one real difference. The Claude Code CLI runs its own agent loop and
calls tools through an MCP bridge, so the runner hands it the tools once and reads the
whole conversation off one process. The HTTP providers stop at each tool call and the
runner drives the next round itself.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class Msg:
    """Provider-agnostic transcript entry."""
    role: str                                   # user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = False


class ChatModel(Protocol):
    provider: str
    model: str
    native_loop: bool

    def stream(self, *, system: str, messages: list[Msg], tools: list[ToolSpec],
               temperature: float, max_tokens: int) -> AsyncIterator[dict[str, Any]]: ...


class LLMUnavailable(Exception):
    code = "llm_unavailable"


def render_transcript(messages: list[Msg]) -> str:
    """Flatten a transcript for backends that take one prompt string (the CLI).

    Tool traffic is dropped: the CLI re-runs its own tool calls, and replaying ours would
    describe work it did not do.
    """
    out = []
    for m in messages:
        if m.role == "user":
            out.append(f"[사용자]\n{m.content}")
        elif m.role == "assistant" and m.content:
            out.append(f"[assistant]\n{m.content}")
    return "\n\n".join(out)
