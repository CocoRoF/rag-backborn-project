"""Deterministic offline model. Tests and keyless smoke runs, never a fallback in production."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ragb.providers.llm.base import Msg, ToolSpec


class FakeChat:
    provider = "fake"
    native_loop = False

    def __init__(self, model: str = "fake-1"):
        self.model = model
        self._calls = 0

    async def stream(self, *, system: str, messages: list[Msg], tools: list[ToolSpec],
                     temperature: float, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        self._calls += 1
        # First round with tools available: exercise the tool path once, then answer.
        if tools and self._calls == 1 and any(t.name == "rag_search" for t in tools):
            yield {"type": "tool_call", "id": "fake_1", "name": "rag_search", "input": {"query": last[:200]}}
            return
        yield {"type": "text", "text": f"[fake] {last[:400]}"}
        yield {"type": "usage", "input_tokens": 10, "output_tokens": 10}
