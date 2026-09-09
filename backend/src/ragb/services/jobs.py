"""PG-backed job queue (SKIP LOCKED). Producers call ``enqueue``; the worker polls."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.models import Job

_ACTIVE_DEDUPE_PREDICATE = "dedupe_key IS NOT NULL AND status IN ('queued', 'running')"


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    delay_s: float = 0,
    priority: int = 5,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> Job | None:
    values = {
        "kind": kind,
        "payload": payload or {},
        "run_at": datetime.now(UTC) + timedelta(seconds=delay_s),
        "priority": priority,
        "dedupe_key": dedupe_key,
        "max_attempts": max_attempts,
        "created_at": datetime.now(UTC),
    }
    if dedupe_key:
        # The partial unique index is the authority. This remains correct when
        # multiple API/worker/scheduler processes enqueue the same key at once;
        # no application-level read-before-insert race remains.
        stmt = (
            pg_insert(Job)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[Job.dedupe_key],
                index_where=text(_ACTIVE_DEDUPE_PREDICATE),
            )
            .returning(Job.id)
        )
        row = (await db.execute(stmt)).first()
        if row is None:
            return None
        return await db.get(Job, row[0])

    job = Job(**values)
    db.add(job)
    return job


async def claim(db: AsyncSession, worker_id: str, kinds: list[str] | None = None) -> Job | None:
    kind_clause = "AND kind = ANY(:kinds)" if kinds else ""
    sql = text(f"""
        UPDATE jobs SET status='running', locked_by=:w, locked_at=now(), attempts=attempts+1
        WHERE id = (
            SELECT id FROM jobs WHERE status='queued' AND run_at <= now() {kind_clause}
            ORDER BY priority ASC, run_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED
        ) RETURNING id
    """)
    params: dict[str, Any] = {"w": worker_id}
    if kinds:
        params["kinds"] = kinds
    row = (await db.execute(sql, params)).first()
    if not row:
        return None
    return await db.get(Job, row[0])


async def requeue_stale(db: AsyncSession, *, minutes: int = 20) -> int:
    """Jobs left in 'running' by a crashed worker go back to the queue (they count as an attempt)."""
    r = await db.execute(text("""
        UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
            locked_by = NULL, locked_at = NULL, last_error = coalesce(last_error, 'requeued: worker lost'),
            finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE finished_at END
        WHERE status = 'running' AND locked_at < now() - make_interval(mins => :m)
    """), {"m": minutes})
    return int(r.rowcount or 0)


async def finish(db: AsyncSession, job: Job, *, result: dict | None = None) -> None:
    job.status = "done"
    job.finished_at = datetime.now(UTC)
    job.result = result


async def fail(db: AsyncSession, job: Job, error: str) -> None:
    job.last_error = error[:4000]
    if job.attempts >= job.max_attempts:
        job.status = "dead"
        job.finished_at = datetime.now(UTC)
    else:
        job.status = "queued"
        job.run_at = datetime.now(UTC) + timedelta(seconds=min(600, 5 * (2 ** job.attempts)))
    job.locked_by = None
    job.locked_at = None
