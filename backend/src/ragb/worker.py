"""The background worker: indexing jobs, stale-job recovery, log retention, credential backup."""
from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete

from ragb.config import get_settings
from ragb.core.logging import configure, get_logger
from ragb.db.session import session_scope
from ragb.models import Job
from ragb.services import jobs as J

log = get_logger("ragb.worker")
HANDLERS = {}
_stop = asyncio.Event()


def handler(kind: str):
    def wrap(fn):
        HANDLERS[kind] = fn
        return fn
    return wrap


@handler("index.node")
async def _index(db, payload):
    from ragb.services.indexing import index_node
    return await index_node(db, uuid.UUID(payload["node_id"]))


@handler("plugin.run")
async def _plugin_run(db, payload):
    from ragb.services.pipeline import execute
    return await execute(db, uuid.UUID(payload["run_id"]))


async def _tick_maintenance() -> None:
    async with session_scope() as db:
        n = await J.requeue_stale(db, minutes=20)
        if n:
            log.info("requeued stale jobs", count=n)
        from ragb.models import AuditLog, Job, LlmCall, RetrievalLog
        from ragb.services import claude_code
        from ragb.services import settings as S
        days = int(await S.get(db, "log.retention_days") or 30)
        cutoff = datetime.now(UTC) - timedelta(days=days)
        for model in (AuditLog, LlmCall, RetrievalLog):
            await db.execute(delete(model).where(model.created_at < cutoff))
        await db.execute(delete(Job).where(Job.status.in_(("done", "dead")), Job.created_at < cutoff))
        with contextlib.suppress(Exception):
            # The CLI refreshes its own token; copying the refreshed file into settings is
            # what makes a recreated volume survivable.
            await claude_code.backup_credentials(db)


async def run() -> None:
    configure()
    s = get_settings()
    alive = Path(s.data_dir) / ".worker-alive"
    alive.parent.mkdir(parents=True, exist_ok=True)
    log.info("worker started", worker_id=s.worker_id, kinds=list(HANDLERS))
    last_maintenance = 0.0
    while not _stop.is_set():
        alive.touch()
        if time.monotonic() - last_maintenance > 300:
            last_maintenance = time.monotonic()
            with contextlib.suppress(Exception):
                await _tick_maintenance()
        job = None
        try:
            async with session_scope() as db:
                job = await J.claim(db, s.worker_id, list(HANDLERS))
                if job is not None:
                    job_id, kind, payload = job.id, job.kind, dict(job.payload or {})
        except Exception as e:  # noqa: BLE001
            log.warning("claim failed", err=str(e)[:200])
            await asyncio.sleep(3)
            continue
        if job is None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(_stop.wait(), timeout=2.0)
            continue
        t0 = time.monotonic()
        try:
            async with session_scope() as db:
                result = await HANDLERS[kind](db, payload)
                fresh = await db.get(Job, job_id)
                await J.finish(db, fresh, result=result if isinstance(result, dict) else {"ok": True})
            log.info("job done", kind=kind, ms=int((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            log.warning("job failed", kind=kind, err=str(e)[:300])
            with contextlib.suppress(Exception):
                async with session_scope() as db:
                    fresh = await db.get(Job, job_id)
                    if fresh is not None:
                        await J.fail(db, fresh, f"{e.__class__.__name__}: {e}")
    log.info("worker stopped")


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop.set)
    loop.run_until_complete(run())


if __name__ == "__main__":
    main()
