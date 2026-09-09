"""Running plug-ins: queue a run, execute it in the worker, keep the log.

Runs go through the job queue rather than the request. A SOURCE plug-in over 500 rows or a
SCORE plug-in over 30 records is minutes of model calls, and an HTTP request is the wrong
place to hold that — a browser tab closing must not abort a pipeline halfway through.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.errors import Conflict, NotFound, ValidationFailed
from ragb.core.logging import get_logger
from ragb.models import PluginBinding, PluginRun, Repository
from ragb.plugins import PluginError, RunContext
from ragb.plugins import get as get_plugin
from ragb.services import jobs as J

log = get_logger("ragb.pipeline")


def spec_of(plugin_id: str):
    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise NotFound(f"플러그인을 찾을 수 없습니다: {plugin_id}", code="plugin_not_found")
    return plugin.spec


def coerce(spec, config: dict[str, Any]) -> dict[str, Any]:
    """Validate against the plug-in's declared fields. A form and the code that reads it come
    from the same declaration, so this is where a mismatch surfaces — not at run time."""
    out: dict[str, Any] = {}
    for f in spec.fields:
        raw = config.get(f.key, f.default)
        if f.type == "number":
            try:
                raw = float(raw) if raw not in (None, "") else f.default
                raw = int(raw) if float(raw).is_integer() else raw
            except (TypeError, ValueError):
                raise ValidationFailed(f"'{f.label}' 은 숫자여야 합니다", code="bad_config") from None
        elif f.type == "bool":
            raw = raw in (True, "true", "True", 1, "1")
        elif raw is not None:
            raw = str(raw)
        if f.required and raw in (None, "", []):
            raise ValidationFailed(f"'{f.label}' 은 필수입니다", code="config_required")
        out[f.key] = raw
    return out


async def create_binding(db: AsyncSession, repo: Repository, *, plugin_id: str, name: str = "",
                         config: dict | None = None) -> PluginBinding:
    spec = spec_of(plugin_id)
    binding = PluginBinding(repository_id=repo.id, plugin_id=plugin_id, name=(name or spec.name)[:120],
                            config=coerce(spec, config or {}))
    db.add(binding)
    await db.flush()
    return binding


async def get_binding(db: AsyncSession, repo: Repository, binding_id: uuid.UUID) -> PluginBinding:
    b = await db.get(PluginBinding, binding_id)
    if b is None or b.repository_id != repo.id:
        raise NotFound("파이프라인 단계를 찾을 수 없습니다", code="binding_not_found")
    return b


async def queue_run(db: AsyncSession, repo: Repository, binding: PluginBinding,
                    actor_id: uuid.UUID | None = None) -> PluginRun:
    if not binding.enabled:
        raise Conflict("비활성 단계입니다", code="binding_disabled")
    busy = (await db.execute(select(PluginRun).where(
        PluginRun.binding_id == binding.id, PluginRun.status.in_(("queued", "running"))))).scalars().first()
    if busy is not None:
        raise Conflict("이미 실행 중입니다", code="run_in_progress")
    run = PluginRun(repository_id=repo.id, binding_id=binding.id, plugin_id=binding.plugin_id,
                    status="queued", config=dict(binding.config or {}), started_by=actor_id,
                    created_at=datetime.now(UTC))
    db.add(run)
    await db.flush()
    await J.enqueue(db, "plugin.run", {"run_id": str(run.id)}, priority=4, dedupe_key=f"plugin:{run.id}")
    return run


async def execute(db: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Worker handler. Never raises for a plug-in's own failure — a configuration mistake is
    a result to read on the run, not a job to retry five times."""
    run = await db.get(PluginRun, run_id)
    if run is None:
        return {"skipped": "missing"}
    repo = await db.get(Repository, run.repository_id)
    plugin = get_plugin(run.plugin_id)
    if repo is None or plugin is None:
        run.status, run.error = "failed", "저장소 또는 플러그인이 없습니다"
        run.finished_at = datetime.now(UTC)
        return {"failed": run.error}

    run.status = "running"
    await db.flush()
    ctx = RunContext(db=db, repository=repo, config=dict(run.config or {}), run_id=run.id,
                     actor_id=run.started_by)
    try:
        result = await plugin.run(ctx)
        run.status, run.summary, run.counts = "done", result.summary[:2000], result.counts
    except PluginError as e:
        run.status, run.error = "failed", str(e)[:2000]
        ctx.log(f"실패: {e}")
    except Exception as e:  # noqa: BLE001
        run.status, run.error = "failed", f"{type(e).__name__}: {str(e)[:1500]}"
        ctx.log(f"오류: {run.error}")
        log.warning("plugin run failed", plugin=run.plugin_id, err=run.error)
    finally:
        run.log = ctx.log_text[-20000:]
        run.finished_at = datetime.now(UTC)
        if run.binding_id:
            b = await db.get(PluginBinding, run.binding_id)
            if b is not None:
                b.last_run_at, b.last_status = run.finished_at, run.status
    return {"status": run.status, "summary": run.summary, "counts": run.counts}
