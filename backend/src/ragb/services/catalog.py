"""Model catalog. Seeded on boot; the admin console enables/disables rows and picks a default."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.models import ModelCatalog

# (provider, model_id, display, cli_alias, context, max_out, tools, vision)
#
# `cli_alias` is what the Claude Code CLI is launched with *instead of* model_id, so a
# pinned row must leave it empty — otherwise the console names one model and the CLI runs
# whatever the alias resolves to today. The alias rows below are the deliberate
# "always the latest" choice.
SEED = [
    ("claude_code", "claude-sonnet-5", "Claude Sonnet 5 (Claude Code)", None, 1_000_000, 64_000, True, True),
    ("claude_code", "claude-opus-5", "Claude Opus 5 (Claude Code)", None, 1_000_000, 64_000, True, True),
    ("claude_code", "claude-haiku-4-5-20251001", "Claude Haiku 4.5 (Claude Code)", None, 200_000, 64_000, True, True),
    ("claude_code", "sonnet", "Sonnet — 최신 (Claude Code)", "sonnet", 1_000_000, 64_000, True, True),
    ("claude_code", "haiku", "Haiku — 최신 (Claude Code)", "haiku", 200_000, 64_000, True, True),
    ("anthropic", "claude-sonnet-5", "Claude Sonnet 5", None, 1_000_000, 64_000, True, True),
    ("anthropic", "claude-opus-5", "Claude Opus 5", None, 1_000_000, 64_000, True, True),
    ("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5", None, 200_000, 64_000, True, True),
    ("openai", "gpt-5.6-terra", "GPT-5.6 terra", None, 400_000, 128_000, True, True),
    ("openai", "gpt-5.6-luna", "GPT-5.6 luna", None, 400_000, 128_000, True, True),
    ("gemini", "gemini-3.8-flash", "Gemini 3.8 Flash", None, 1_000_000, 65_536, True, True),
    ("gemini", "gemini-3.1-pro-preview", "Gemini 3.1 Pro", None, 1_000_000, 65_536, True, True),
]
DEFAULT_MODEL = ("claude_code", "claude-sonnet-5")


async def seed(db: AsyncSession) -> dict[str, int]:
    existing = {(m.provider, m.model_id): m for m in (await db.execute(select(ModelCatalog))).scalars().all()}
    has_default = any(m.is_default for m in existing.values())
    added = 0
    for i, (prov, mid, disp, alias, ctx, out, tools, vision) in enumerate(SEED):
        if (prov, mid) in existing:
            continue
        row = ModelCatalog(provider=prov, model_id=mid, display_name=disp, cli_alias=alias, context_window=ctx,
                           max_output=out, supports_tools=tools, supports_vision=vision, enabled=True,
                           is_default=(not has_default and (prov, mid) == DEFAULT_MODEL), sort_order=i)
        has_default = has_default or row.is_default
        db.add(row)
        added += 1
    # A pinned row must never carry an alias (see the SEED note).
    repaired = 0
    for (_p, mid), row in existing.items():
        if row.cli_alias and row.cli_alias != mid:
            row.cli_alias = None
            repaired += 1
    return {"added": added, "alias_repaired": repaired}


async def list_models(db: AsyncSession, *, enabled_only: bool = False) -> list[ModelCatalog]:
    stmt = select(ModelCatalog).order_by(ModelCatalog.sort_order, ModelCatalog.provider, ModelCatalog.model_id)
    if enabled_only:
        stmt = stmt.where(ModelCatalog.enabled.is_(True))
    return list((await db.execute(stmt)).scalars().all())


async def get_model(db: AsyncSession, provider: str, model_id: str) -> ModelCatalog | None:
    return (await db.execute(select(ModelCatalog).where(
        ModelCatalog.provider == provider, ModelCatalog.model_id == model_id))).scalars().first()


async def default_model(db: AsyncSession) -> ModelCatalog | None:
    m = (await db.execute(select(ModelCatalog).where(
        ModelCatalog.is_default.is_(True), ModelCatalog.enabled.is_(True)))).scalars().first()
    if m:
        return m
    rows = await list_models(db, enabled_only=True)
    return rows[0] if rows else None


async def resolve(db: AsyncSession, provider: str, model_id: str) -> tuple[ModelCatalog | None, bool]:
    """(row, fell_back). A disabled or unknown model silently becomes the default rather than
    a 500 — an admin disabling a model should not break every agent still pointing at it."""
    m = await get_model(db, provider, model_id)
    if m and m.enabled:
        return m, False
    return await default_model(db), True
