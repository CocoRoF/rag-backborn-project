"""One-shot, non-streaming completion for background work (section summaries, query rewrite)."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.providers.llm import get_chat_model
from ragb.providers.llm.base import Msg


async def complete(db: AsyncSession, *, provider: str, model: str, system: str, user_text: str,
                   max_tokens: int = 800, temperature: float = 0.0, timeout_s: float = 120.0) -> tuple[str, dict]:
    model_obj = await get_chat_model(db, provider, model)

    async def run() -> tuple[str, dict]:
        text, usage = "", {"input_tokens": 0, "output_tokens": 0}
        async for ev in model_obj.stream(system=system, messages=[Msg(role="user", content=user_text)],
                                         tools=[], temperature=temperature, max_tokens=max_tokens):
            if ev["type"] == "text":
                text += ev["text"]
            elif ev["type"] == "usage":
                usage = {"input_tokens": ev.get("input_tokens", 0), "output_tokens": ev.get("output_tokens", 0)}
        return text.strip(), usage

    return await asyncio.wait_for(run(), timeout=timeout_s)
