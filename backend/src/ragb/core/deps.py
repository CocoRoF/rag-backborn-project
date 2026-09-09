from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.errors import Forbidden, Unauthorized
from ragb.core.security import decode_access_token
from ragb.db.session import get_session
from ragb.models import User


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


async def current_user(request: Request, db: AsyncSession = Depends(get_session)) -> User:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthorized("로그인이 필요합니다")
    claims = decode_access_token(auth[7:].strip())
    user = await db.get(User, __import__("uuid").UUID(claims["sub"]))
    if user is None or user.status != "active":
        raise Unauthorized("세션이 유효하지 않습니다")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise Forbidden("관리자 권한이 필요합니다", code="admin_required")
    return user
