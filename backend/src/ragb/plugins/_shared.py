"""Helpers every plug-in reuses: model resolution, JSON coaxing, evidence gathering."""
from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.plugins.base import ConfigField, PluginError, RunContext
from ragb.providers.llm.simple import complete
from ragb.services import retrieval
from ragb.services import settings as S

_JSON_BLOCK = re.compile(r"[\[{].*[\]}]", re.S)

MODEL_FIELDS = [
    ConfigField("provider", "모델 공급자", "select", "",
                hint="비우면 [관리 → 임베딩·RAG]의 요약 모델을 씁니다.",
                options=[{"value": "", "label": "기본값 사용"}] +
                        [{"value": p, "label": p} for p in ("claude_code", "anthropic", "openai", "gemini")]),
    ConfigField("model", "모델", "text", "", hint="비우면 기본 요약 모델."),
]


async def resolve_model(db: AsyncSession, ctx: RunContext) -> tuple[str, str]:
    provider = ctx.opt("provider") or await S.get(db, "rag.section_summary_provider") or "claude_code"
    model = ctx.opt("model") or await S.get(db, "rag.section_summary_model") or ""
    return str(provider), str(model)


async def ask_json(db: AsyncSession, ctx: RunContext, *, system: str, prompt: str,
                   max_tokens: int = 1200) -> Any:
    """One model call that must return JSON. Returns None when it does not — callers treat
    that as "this item could not be processed", never as a silent empty result."""
    provider, model = await resolve_model(db, ctx)
    text, _ = await complete(db, provider=provider, model=model, system=system, user_text=prompt,
                             max_tokens=max_tokens, timeout_s=180)
    return parse_json(text)


def parse_json(text: str) -> Any:
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?", "", body).rstrip("`").strip()
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(body)
    m = _JSON_BLOCK.search(body)
    if m:
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(m.group(0))
    return None


async def gather_evidence(db: AsyncSession, repo_id, query: str, *, k: int = 6,
                          cfg: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Retrieve the passages an assertion will be based on, and the citation list to store
    alongside it. Every plug-in that asks a model to judge something goes through here."""
    cfg = cfg or await S.rag_config(db)
    res = await retrieval.search(db, repository_ids=[repo_id], query=query, cfg=cfg, k=k)
    block = retrieval.format_context(res.passages)
    cites = [{"path": p.node_path, "heading": p.heading_path, "page": p.page,
              "score": round(p.score, 4), "quote": p.text[:400]} for p in res.passages]
    return block, cites


def require_llm(ok: bool) -> None:
    if not ok:
        raise PluginError("모델이 설정되지 않았습니다. [관리 → 공급자·모델]에서 키를 넣거나 "
                          "Claude Code 에 로그인한 뒤 다시 실행하세요.")
