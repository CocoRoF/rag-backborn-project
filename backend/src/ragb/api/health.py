from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.db.session import get_session
from ragb.services import runtime

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_session)):
    await db.execute(text("SELECT 1"))
    return {"ok": True, "active_turns": runtime.active()}
