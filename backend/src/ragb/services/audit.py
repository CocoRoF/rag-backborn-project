from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.models import AuditLog


def record(db: AsyncSession, action: str, *, actor_id: uuid.UUID | None = None, actor_kind: str = "user",
           target_type: str | None = None, target_id: Any = None, ip: str | None = None, ua: str | None = None,
           meta: dict | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, actor_kind=actor_kind, action=action, target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None, ip=ip, ua=(ua or "")[:400],
                    meta=meta or {}, created_at=datetime.now(UTC)))
