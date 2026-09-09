from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.config import get_settings
from ragb.core.deps import client_ip, current_user
from ragb.core.errors import Unauthorized
from ragb.core.security import create_access_token
from ragb.db.session import get_session
from ragb.models import User
from ragb.services import accounts as A
from ragb.services import audit
from ragb.services import settings as S

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "ragb_refresh"


class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""


def _set_cookie(response: Response, raw: str) -> None:
    s = get_settings()
    response.set_cookie(COOKIE, raw, max_age=s.refresh_token_days * 86400, httponly=True, samesite="lax",
                        secure=s.public_url.lower().startswith("https://"), path="/api/auth")


@router.get("/status")
async def status(db: AsyncSession = Depends(get_session)):
    return {"bootstrap_needed": await A.user_count(db) == 0,
            "signup_mode": await S.get(db, "signup.mode"),
            "service_name": await S.get(db, "branding.service_name"),
            "tagline": await S.get(db, "branding.tagline"),
            # Deliberately public: these credentials exist to be handed out. The accounts are
            # only seeded when demo mode is on, so this list is empty on a real deployment.
            "demo_accounts": A.demo_accounts()}


@router.post("/signup")
async def signup(body: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    user = await A.signup(db, email=body.email, password=body.password, name=body.name)
    access, raw = await A.issue_session(db, user, ua=request.headers.get("user-agent", ""), ip=client_ip(request))
    audit.record(db, "auth.signup", actor_id=user.id, ip=client_ip(request), ua=request.headers.get("user-agent"))
    await db.commit()
    _set_cookie(response, raw)
    return {"access_token": access, "user": A.public_user(user)}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    user = await A.authenticate(db, email=body.email, password=body.password)
    access, raw = await A.issue_session(db, user, ua=request.headers.get("user-agent", ""), ip=client_ip(request))
    audit.record(db, "auth.login", actor_id=user.id, ip=client_ip(request), ua=request.headers.get("user-agent"))
    await db.commit()
    _set_cookie(response, raw)
    return {"access_token": access, "user": A.public_user(user)}


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(COOKIE, "")
    if not raw:
        raise Unauthorized("세션이 없습니다", code="no_refresh_cookie")
    access, new_raw, user = await A.rotate_session(db, raw, ua=request.headers.get("user-agent", ""),
                                                   ip=client_ip(request))
    await db.commit()
    _set_cookie(response, new_raw)
    return {"access_token": access, "user": A.public_user(user)}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(COOKIE, "")
    if raw:
        await A.revoke_session(db, raw)
        await db.commit()
    response.delete_cookie(COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {"user": A.public_user(user)}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/password")
async def change_password(body: PasswordChange, user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_session)):
    from ragb.core.errors import ValidationFailed
    from ragb.core.security import hash_password, verify_password
    if not verify_password(body.current_password, user.password_hash):
        raise Unauthorized("현재 비밀번호가 올바르지 않습니다", code="bad_credentials")
    if len(body.new_password) < A.MIN_PASSWORD:
        raise ValidationFailed(f"비밀번호는 {A.MIN_PASSWORD}자 이상이어야 합니다", code="weak_password")
    db_user = await db.get(User, user.id)
    db_user.password_hash = hash_password(body.new_password)
    audit.record(db, "auth.password_change", actor_id=user.id)
    await db.commit()
    return {"ok": True}


@router.post("/token")
async def issue_token(user: User = Depends(current_user)):
    """Refresh the access token without touching the refresh cookie (long-lived tabs)."""
    return {"access_token": create_access_token(user.id, user.role)}
