"""Claude Code CLI credentials: status, device login relay, backup/restore.

The CLI keeps a subscription login in ``~/.claude/.credentials.json`` and refreshes it in
place. Copying another machine's file does not work — the moment the original refreshes,
this copy is dead — so the console runs the device login here and the container keeps its
own lineage. The file also lives in a volume, and a backup in ``system_settings`` survives
the volume being recreated.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.config import get_settings
from ragb.core.errors import Conflict, ValidationFailed
from ragb.core.logging import get_logger
from ragb.providers.llm.claude_code import resolve_binary
from ragb.services import settings as S

log = get_logger("ragb.claude")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")
_version_cache: tuple[float, str] | None = None


def creds_path():
    return get_settings().claude_home / ".credentials.json"


def _home_env() -> dict[str, str]:
    s = get_settings()
    home = str(s.claude_home.parent) if s.claude_home.name == ".claude" else str(s.claude_home)
    return {"HOME": home, "CLAUDE_CONFIG_DIR": str(s.claude_home), "PATH": os.environ.get("PATH", ""),
            "DISABLE_AUTOUPDATER": "1"}


async def cli_version(*, max_age_s: float = 300.0) -> str:
    global _version_cache
    if _version_cache and time.monotonic() - _version_cache[0] < max_age_s:
        return _version_cache[1]
    try:
        proc = await asyncio.create_subprocess_exec(
            resolve_binary(), "--version", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=_home_env())
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        v = out.decode(errors="replace").strip()[:80] or "unknown"
    except FileNotFoundError:
        v = "not installed"
    except Exception as e:  # noqa: BLE001
        v = f"error: {e.__class__.__name__}"
    _version_cache = (time.monotonic(), v)
    return v


def read_credentials() -> dict[str, Any] | None:
    p = creds_path()
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except Exception:
        return None


def _oauth_valid(creds: dict[str, Any] | None) -> bool:
    oauth = (creds or {}).get("claudeAiOauth") if isinstance(creds, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken") or not oauth.get("refreshToken"):
        return False
    exp = oauth.get("expiresAt")
    return not (exp and float(exp) / 1000 < time.time())


def _expires_at(creds: dict[str, Any] | None) -> float:
    try:
        return float(((creds or {}).get("claudeAiOauth") or {}).get("expiresAt") or 0)
    except (TypeError, ValueError):
        return 0.0


async def status(db: AsyncSession) -> dict[str, Any]:
    creds = read_credentials()
    oauth = (creds or {}).get("claudeAiOauth") or {}
    access_exp, session_exp = oauth.get("expiresAt"), oauth.get("refreshTokenExpiresAt")
    # Two clocks. `expiresAt` is the access token the CLI silently refreshes every few hours;
    # `refreshTokenExpiresAt` is how long the login actually lasts. Reporting the first as
    # "expiry" makes a healthy install look dead every morning.
    return {
        "auth_mode": await S.get(db, "providers.claude_code.auth_mode"),
        "binary": resolve_binary(), "version": await cli_version(),
        "credentials_present": creds is not None,
        "access_expires_at": datetime.fromtimestamp(access_exp / 1000, tz=UTC).isoformat() if access_exp else None,
        "session_expires_at": datetime.fromtimestamp(session_exp / 1000, tz=UTC).isoformat() if session_exp else None,
        "expired": bool(creds) and (not oauth.get("refreshToken")
                                    or bool(session_exp and session_exp / 1000 < time.time())),
        "subscription": oauth.get("subscriptionType"),
        "has_setup_token": bool(await S.get(db, "providers.claude_code.setup_token")),
        "has_anthropic_key": bool(await S.get(db, "providers.anthropic.api_key")),
        "login_running": bool(_job and not _job.done),
    }


async def import_credentials(db: AsyncSession, raw_json: str, *, updated_by=None) -> dict[str, Any]:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValidationFailed("JSON 형식이 아닙니다", code="invalid_json") from e
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not oauth or not oauth.get("accessToken") or not oauth.get("refreshToken"):
        raise ValidationFailed("claudeAiOauth.accessToken/refreshToken 이 없습니다", code="invalid_credentials")
    _write(data)
    await S.put(db, "providers.claude_code.credentials_json", json.dumps(data), updated_by=updated_by)
    await S.put(db, "providers.claude_code.auth_mode", "oauth", updated_by=updated_by)
    return await status(db)


def _write(data: dict[str, Any]) -> None:
    p = creds_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


async def backup_credentials(db: AsyncSession) -> bool:
    """Copy the CLI's refreshed file into settings — never an expired or older one over a good one."""
    creds = read_credentials()
    if not _oauth_valid(creds):
        return False
    raw_prev = await S.get(db, "providers.claude_code.credentials_json", use_cache=False)
    try:
        prev = json.loads(raw_prev) if raw_prev else None
    except json.JSONDecodeError:
        prev = None
    if prev == creds or (prev is not None and _expires_at(prev) > _expires_at(creds)):
        return False
    await S.put(db, "providers.claude_code.credentials_json", json.dumps(creds))
    return True


async def restore_credentials(db: AsyncSession) -> bool:
    raw = await S.get(db, "providers.claude_code.credentials_json", use_cache=False)
    if not raw:
        return False
    try:
        stored = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not _oauth_valid(stored):
        return False
    current = read_credentials()
    if _oauth_valid(current) and _expires_at(current) >= _expires_at(stored):
        return False
    _write(stored)
    return True


# ── device login relay ──────────────────────────────────────────────────────────
#
# Plain pipes, not a pty: `claude auth login` prints the OAuth URL and then blocks on stdin
# for the code pasted back from the browser. A pty accepts the code too, but wraps the URL
# in an OSC-8 hyperlink and echoes it twice, which fills the console pane with escape noise.


class LoginJob:
    def __init__(self, argv: list[str]):
        self.argv = argv
        self.lines: list[dict[str, Any]] = []
        self.done = False
        self.error = ""
        self.proc: asyncio.subprocess.Process | None = None

    def _push(self, text: str) -> None:
        clean = _ANSI.sub("", text).strip()
        if clean:
            self.lines.append({"seq": len(self.lines), "text": clean[:2000],
                               "at": datetime.now(UTC).isoformat()})
            del self.lines[:-200]

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=_home_env())
        asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                raw = await self.proc.stdout.read(4096)
                if not raw:
                    break
                self._push(raw.decode(errors="replace"))
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:300]
        finally:
            rc = await self.proc.wait()
            self._push(f"[프로세스 종료 코드 {rc}]")
            self.done = True

    async def send(self, text: str) -> None:
        if self.proc is None or self.proc.stdin is None or self.done:
            raise Conflict("로그인 세션이 끝났습니다", code="login_finished")
        self.proc.stdin.write((text.strip() + "\n").encode())
        await self.proc.stdin.drain()

    async def cancel(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
        self.done = True


_job: LoginJob | None = None


async def start_login(*, console: bool = False) -> dict[str, Any]:
    global _job
    if _job is not None and not _job.done:
        raise Conflict("이미 로그인 진행 중입니다", code="login_in_progress")
    argv = [resolve_binary(), "auth", "login"]
    if console:
        argv.append("--console")
    _job = LoginJob(argv)
    await _job.start()
    return login_state()


def login_state() -> dict[str, Any]:
    if _job is None:
        return {"running": False, "lines": []}
    return {"running": not _job.done, "done": _job.done, "error": _job.error, "lines": _job.lines}


async def send_login_input(text: str) -> dict[str, Any]:
    if _job is None:
        raise Conflict("진행 중인 로그인이 없습니다", code="no_login")
    await _job.send(text)
    return login_state()


async def cancel_login() -> dict[str, Any]:
    if _job is not None:
        await _job.cancel()
    return login_state()
