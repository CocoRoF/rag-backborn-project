"""Password hashing, JWT, refresh tokens, at-rest encryption."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ragb.config import get_settings
from ragb.core.errors import Unauthorized

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_DEV_SECRET = "dev-secret-change-me-dev-secret-change-me"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, h: str | None) -> bool:
    if not h:
        return False
    try:
        return _ph.verify(h, pw)
    except (VerifyMismatchError, Exception):
        return False


def _hkdf(label: str, length: int = 32) -> bytes:
    ikm = get_settings().secret_key.encode()
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"ragb-v1", info=label.encode()).derive(ikm)


def _jwt_key(audience: str) -> bytes:
    return _hkdf(f"jwt:{audience}")


def legacy_fernet_key() -> str:
    return base64.urlsafe_b64encode(_hkdf("fernet")).decode()


def _fernets() -> list[Fernet]:
    s = get_settings()
    keys = [s.encryption_key.strip() or legacy_fernet_key()]
    for extra in s.encryption_key_previous.split(","):
        extra = extra.strip()
        if extra and extra not in keys:
            keys.append(extra)
    try:
        return [Fernet(k.encode()) for k in keys]
    except (ValueError, TypeError) as e:
        raise RuntimeError("RAGB_ENCRYPTION_KEY must be a urlsafe-base64 32-byte Fernet key") from e


def validate_security_settings() -> None:
    """Fail closed on a production deployment still running the shipped dev secret."""
    s = get_settings()
    if s.public_url.lower().startswith("https://") and (
        s.secret_key == _DEV_SECRET or len(s.secret_key.encode()) < 32
    ):
        raise RuntimeError("RAGB_SECRET_KEY must be a unique 32+ byte secret when RAGB_PUBLIC_URL is https")
    _fernets()


def encrypt(text: str) -> str:
    return _fernets()[0].encrypt((text or "").encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    for f in _fernets():
        try:
            return f.decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    raise ValueError("decrypt failed")


def sha256(s: str | bytes) -> str:
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id), "role": role, "aud": "user", "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_minutes)).timestamp()), "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, _jwt_key("user"), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _jwt_key("user"), algorithms=["HS256"], audience="user")
    except jwt.ExpiredSignatureError as e:
        raise Unauthorized("token expired", code="token_expired") from e
    except jwt.PyJWTError as e:
        raise Unauthorized("invalid token", code="invalid_token") from e


def new_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, sha256(raw)


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
