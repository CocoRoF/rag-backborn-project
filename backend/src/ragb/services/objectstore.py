"""Where uploaded bytes actually live.

Two backends behind one interface. Local disk is the default and needs nothing; S3 is what
makes this survive more than one machine — a pod that writes to its own filesystem serves a
file the next pod cannot find, and the bug looks like "images sometimes 404".

`storage_path` on the row keeps its meaning either way: a filesystem path for local, or an
`s3://bucket/key` URI. Old rows are filesystem paths and keep working, so switching the
backend does not orphan what was already uploaded.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from ragb.config import get_settings
from ragb.core.logging import get_logger

log = get_logger("ragb.objectstore")
S3_PREFIX = "s3://"


def s3_enabled() -> bool:
    s = get_settings()
    return bool(getattr(s, "s3_endpoint", "") and getattr(s, "s3_bucket", ""))


def _client() -> Any:
    import boto3
    from botocore.config import Config
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key or None,
        aws_secret_access_key=s.s3_secret_key or None,
        region_name=s.s3_region or "us-east-1",
        # SeaweedFS, MinIO and friends serve one host with the bucket in the path.
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3, "mode": "standard"}),
    )


def _put_sync(key: str, data: bytes, mime: str) -> None:
    s = get_settings()
    c = _client()
    with contextlib.suppress(Exception):        # idempotent: fine if it already exists
        c.create_bucket(Bucket=s.s3_bucket)
    c.put_object(Bucket=s.s3_bucket, Key=key, Body=data, ContentType=mime)


def _get_sync(key: str) -> bytes:
    s = get_settings()
    return _client().get_object(Bucket=s.s3_bucket, Key=key)["Body"].read()


def _delete_sync(key: str) -> None:
    s = get_settings()
    with contextlib.suppress(Exception):
        _client().delete_object(Bucket=s.s3_bucket, Key=key)


async def put(key: str, data: bytes, mime: str, *, local_path: Path | None = None) -> str:
    """Store bytes and return what belongs in `storage_path`."""
    if s3_enabled():
        await asyncio.to_thread(_put_sync, key, data, mime)
        return f"{S3_PREFIX}{get_settings().s3_bucket}/{key}"
    path = local_path or (get_settings().upload_root / key)
    await asyncio.to_thread(_write_local, path, data)
    return str(path)


def _write_local(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def get(storage_path: str) -> bytes:
    if storage_path.startswith(S3_PREFIX):
        _, key = _split(storage_path)
        return await asyncio.to_thread(_get_sync, key)
    return await asyncio.to_thread(Path(storage_path).read_bytes)


async def delete(storage_path: str) -> None:
    if storage_path.startswith(S3_PREFIX):
        _, key = _split(storage_path)
        await asyncio.to_thread(_delete_sync, key)
        return
    with contextlib.suppress(FileNotFoundError, OSError):
        await asyncio.to_thread(Path(storage_path).unlink)


def _split(uri: str) -> tuple[str, str]:
    rest = uri[len(S3_PREFIX):]
    bucket, _, key = rest.partition("/")
    return bucket, key


async def health() -> dict[str, Any]:
    """What the admin console shows: which backend is live, and whether it answers."""
    if not s3_enabled():
        root = get_settings().upload_root
        return {"backend": "local", "ok": root.exists(), "detail": str(root)}
    s = get_settings()
    try:
        await asyncio.to_thread(lambda: _client().head_bucket(Bucket=s.s3_bucket))
        return {"backend": "s3", "ok": True, "detail": f"{s.s3_endpoint}/{s.s3_bucket}"}
    except Exception as e:  # noqa: BLE001
        return {"backend": "s3", "ok": False, "detail": f"{type(e).__name__}: {str(e)[:160]}"}
