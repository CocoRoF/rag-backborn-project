"""Sign-up, sign-in, refresh rotation, and the seeded first administrator."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.config import get_settings
from ragb.core.errors import Conflict, Forbidden, Unauthorized, ValidationFailed
from ragb.core.security import create_access_token, hash_password, new_refresh_token, sha256, verify_password
from ragb.models import RefreshToken, User
from ragb.services import settings as S

MIN_PASSWORD = 8


def _normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise ValidationFailed("이메일 형식이 올바르지 않습니다", code="invalid_email")
    return email[:200]


async def user_count(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(User))).scalar_one())


async def signup(db: AsyncSession, *, email: str, password: str, name: str = "") -> User:
    email = _normalise_email(email)
    if len(password or "") < MIN_PASSWORD:
        raise ValidationFailed(f"비밀번호는 {MIN_PASSWORD}자 이상이어야 합니다", code="weak_password")
    mode = await S.get(db, "signup.mode")
    first = await user_count(db) == 0
    if mode != "open" and not first:
        raise Forbidden("현재 가입이 닫혀 있습니다", code="signup_closed")
    if (await db.execute(select(User.id).where(User.email == email))).first() is not None:
        raise Conflict("이미 가입된 이메일입니다", code="email_taken")
    user = User(email=email, name=(name or email.split("@")[0])[:120], password_hash=hash_password(password),
                role="admin" if first else "user")
    db.add(user)
    await db.flush()
    return user


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    user = (await db.execute(select(User).where(User.email == _normalise_email(email)))).scalars().first()
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("이메일 또는 비밀번호가 올바르지 않습니다", code="bad_credentials")
    if user.status != "active":
        raise Forbidden("정지된 계정입니다", code="account_disabled")
    user.last_login_at = datetime.now(UTC)
    return user


async def issue_session(db: AsyncSession, user: User, *, ua: str = "", ip: str = "") -> tuple[str, str]:
    raw, digest = new_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=digest,
                        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days),
                        ua=ua[:300], ip=ip[:64], created_at=datetime.now(UTC)))
    return create_access_token(user.id, user.role), raw


async def rotate_session(db: AsyncSession, raw_token: str, *, ua: str = "", ip: str = "") -> tuple[str, str, User]:
    """Single-use refresh tokens: the old row is revoked as the new one is minted."""
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256(raw_token)))).scalars().first()
    if row is None or row.revoked_at is not None or row.expires_at < datetime.now(UTC):
        raise Unauthorized("세션이 만료되었습니다", code="refresh_invalid")
    user = await db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise Unauthorized("세션이 유효하지 않습니다", code="refresh_invalid")
    row.revoked_at = datetime.now(UTC)
    access, raw = await issue_session(db, user, ua=ua, ip=ip)
    return access, raw, user


async def revoke_session(db: AsyncSession, raw_token: str) -> None:
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256(raw_token)))).scalars().first()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


async def seed_admin(db: AsyncSession) -> uuid.UUID | None:
    """Idempotent, and it never overwrites an existing account — changing the seeded
    password from the console must survive the next boot."""
    s = get_settings()
    if not s.default_admin_enabled:
        return None
    email = _normalise_email(s.default_admin_email)
    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if existing is not None:
        return existing.id
    user = User(email=email, name=s.default_admin_name, password_hash=hash_password(s.default_admin_password),
                role="admin")
    db.add(user)
    await db.flush()
    return user.id


def demo_accounts() -> list[dict[str, str]]:
    """Parsed `email:password:role:label` tuples. Empty when demo mode is off."""
    s = get_settings()
    if not s.demo_mode:
        return []
    out = []
    for entry in s.demo_accounts.split(","):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append({"email": parts[0], "password": parts[1],
                        "role": parts[2] if len(parts) > 2 else "user",
                        "label": parts[3] if len(parts) > 3 else ""})
    return out


async def seed_demo_accounts(db: AsyncSession) -> int:
    """Idempotent, and it never touches an existing account — including its password. Someone
    poking at a demo will change things; the next deploy must not quietly undo that."""
    created = 0
    for acc in demo_accounts():
        email = _normalise_email(acc["email"])
        if (await db.execute(select(User.id).where(User.email == email))).first() is not None:
            continue
        db.add(User(email=email, name=acc["label"] or email.split("@")[0],
                    password_hash=hash_password(acc["password"]),
                    role="admin" if acc["role"] == "admin" else "user"))
        created += 1
    if created:
        await db.flush()
    return created


def public_user(user: User) -> dict:
    return {"id": str(user.id), "email": user.email, "name": user.name, "role": user.role,
            "status": user.status, "created_at": user.created_at.isoformat() if user.created_at else None}
