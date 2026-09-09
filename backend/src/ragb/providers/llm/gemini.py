"""Google Gemini generateContent (public), streaming with function calls."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ragb.providers.llm.base import Msg, ToolSpec

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's function declarations take a strict OpenAPI subset — unknown keys are rejected."""
    allowed = {"type", "description", "properties", "required", "items", "enum", "nullable"}
    out = {k: v for k, v in schema.items() if k in allowed}
    if "properties" in out:
        out["properties"] = {k: _clean_schema(v) if isinstance(v, dict) else v for k, v in out["properties"].items()}
    if isinstance(out.get("items"), dict):
        out["items"] = _clean_schema(out["items"])
    return out


class GeminiChat:
    provider = "gemini"
    native_loop = False

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model

    def _to_api(self, messages: list[Msg]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append({"role": "user", "parts": [{"functionResponse": {
                    "name": m.tool_name, "response": {"result": m.content[:60000]}}}]})
            elif m.role == "assistant":
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                parts += [{"functionCall": {"name": c.name, "args": c.input}} for c in m.tool_calls]
                out.append({"role": "model", "parts": parts or [{"text": "…"}]})
            else:
                out.append({"role": "user", "parts": [{"text": m.content or "…"}]})
        return out

    async def stream(self, *, system: str, messages: list[Msg], tools: list[ToolSpec],
                     temperature: float, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "contents": self._to_api(messages),
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": _clean_schema(t.input_schema)} for t in tools]}]
        url = f"{BASE}/{self.model}:streamGenerateContent"
        seq = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=60, pool=10)) as client:
            async with client.stream("POST", url, params={"key": self.key, "alt": "sse"}, json=body) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"gemini HTTP {r.status_code}: {(await r.aread()).decode()[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    for cand in ev.get("candidates") or []:
                        for part in ((cand.get("content") or {}).get("parts") or []):
                            if part.get("text"):
                                yield {"type": "text", "text": part["text"]}
                            fc = part.get("functionCall")
                            if fc:
                                seq += 1
                                yield {"type": "tool_call", "id": f"gem_{seq}", "name": fc.get("name", ""),
                                       "input": fc.get("args") or {}}
                    u = ev.get("usageMetadata") or {}
                    if u:
                        yield {"type": "usage", "input_tokens": int(u.get("promptTokenCount", 0) or 0),
                               "output_tokens": int(u.get("candidatesTokenCount", 0) or 0)}
