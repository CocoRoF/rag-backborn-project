"""Anthropic Messages API (public), streaming with tool use."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ragb.providers.llm.base import Msg, ToolSpec

API = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"


class AnthropicChat:
    provider = "anthropic"
    native_loop = False

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model

    def _to_api(self, messages: list[Msg]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content[:60000]}
                if m.is_error:
                    block["is_error"] = True
                # Consecutive tool results belong in one user turn, or the API rejects the shape.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue
            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in m.tool_calls]
                out.append({"role": "assistant", "content": blocks})
                continue
            out.append({"role": m.role, "content": m.content or "…"})
        return out

    async def stream(self, *, system: str, messages: list[Msg], tools: list[ToolSpec],
                     temperature: float, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": self._to_api(messages), "stream": True,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        headers = {"x-api-key": self.key, "anthropic-version": VERSION, "content-type": "application/json"}
        partial: dict[int, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=60, pool=10)) as client:
            async with client.stream("POST", API, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"anthropic HTTP {r.status_code}: {(await r.aread()).decode()[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    kind = ev.get("type")
                    if kind == "content_block_start":
                        block = ev.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            partial[ev["index"]] = {"id": block.get("id", ""), "name": block.get("name", ""), "json": ""}
                    elif kind == "content_block_delta":
                        d = ev.get("delta") or {}
                        if d.get("type") == "text_delta":
                            yield {"type": "text", "text": d.get("text", "")}
                        elif d.get("type") == "thinking_delta":
                            yield {"type": "thinking", "text": d.get("thinking", "")}
                        elif d.get("type") == "input_json_delta" and ev["index"] in partial:
                            partial[ev["index"]]["json"] += d.get("partial_json", "")
                    elif kind == "content_block_stop" and ev.get("index") in partial:
                        p = partial.pop(ev["index"])
                        try:
                            args = json.loads(p["json"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        yield {"type": "tool_call", "id": p["id"], "name": p["name"], "input": args}
                    elif kind == "message_delta":
                        u = ev.get("usage") or {}
                        if u:
                            yield {"type": "usage", "input_tokens": int(u.get("input_tokens", 0) or 0),
                                   "output_tokens": int(u.get("output_tokens", 0) or 0)}
                    elif kind == "message_start":
                        u = ((ev.get("message") or {}).get("usage")) or {}
                        if u:
                            yield {"type": "usage", "input_tokens": int(u.get("input_tokens", 0) or 0),
                                   "output_tokens": int(u.get("output_tokens", 0) or 0)}
                    elif kind == "error":
                        raise RuntimeError(f"anthropic: {(ev.get('error') or {}).get('message', 'stream error')}")
