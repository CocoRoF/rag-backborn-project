"""OpenAI Chat Completions (public), streaming with tool calls."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ragb.providers.llm.base import Msg, ToolSpec

API = "https://api.openai.com/v1/chat/completions"


class OpenAIChat:
    provider = "openai"
    native_loop = False

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model

    def _to_api(self, system: str, messages: list[Msg]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content[:60000]})
            elif m.role == "assistant" and m.tool_calls:
                out.append({"role": "assistant", "content": m.content or None,
                            "tool_calls": [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)}}
                                           for c in m.tool_calls]})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def stream(self, *, system: str, messages: list[Msg], tools: list[ToolSpec],
                     temperature: float, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.model, "messages": self._to_api(system, messages), "stream": True,
            "stream_options": {"include_usage": True}, "max_completion_tokens": max_tokens,
        }
        # Reasoning models reject an explicit temperature; the default is the only value.
        if not self.model.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6")):
            body["temperature"] = temperature
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.input_schema}} for t in tools]
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        acc: dict[int, dict[str, str]] = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=60, pool=10)) as client:
            async with client.stream("POST", API, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"openai HTTP {r.status_code}: {(await r.aread()).decode()[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("usage"):
                        u = ev["usage"]
                        yield {"type": "usage", "input_tokens": int(u.get("prompt_tokens", 0) or 0),
                               "output_tokens": int(u.get("completion_tokens", 0) or 0)}
                    for choice in ev.get("choices") or []:
                        d = choice.get("delta") or {}
                        if d.get("content"):
                            yield {"type": "text", "text": d["content"]}
                        if d.get("reasoning_content"):
                            yield {"type": "thinking", "text": d["reasoning_content"]}
                        for tc in d.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            slot = acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                        if choice.get("finish_reason") == "tool_calls":
                            for _, slot in sorted(acc.items()):
                                try:
                                    args = json.loads(slot["args"] or "{}")
                                except json.JSONDecodeError:
                                    args = {}
                                yield {"type": "tool_call", "id": slot["id"] or slot["name"], "name": slot["name"], "input": args}
                            acc.clear()
