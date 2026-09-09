#!/usr/bin/env python3
"""Stdio MCP server that forwards every call to RAG Backborn's loopback HTTP endpoint.

Spawned by ``claude --mcp-config``. Deliberately stdlib-only: it must run under whatever
``python3`` the image has, with no install step at spawn time.

Env (set by ragb.providers.llm.claude_code.mcp_config):
  RAGB_MCP_URL         base URL          (default http://127.0.0.1:8000)
  RAGB_MCP_TOKEN       bearer token      (required)
  RAGB_MCP_SESSION_ID  turn session id   (required)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_URL = os.environ.get("RAGB_MCP_URL", "http://127.0.0.1:8000").rstrip("/")
_TOKEN = os.environ.get("RAGB_MCP_TOKEN", "")
_SESSION = os.environ.get("RAGB_MCP_SESSION_ID", "")
_TIMEOUT = float(os.environ.get("RAGB_MCP_TIMEOUT_S", "300"))


def _arm_parent_death_signal() -> None:
    """If the CLI dies mid-call the stdin loop can wedge on epoll instead of seeing EOF,
    leaving an orphan holding an HTTP connection. Ask the kernel to signal us instead."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0)   # PR_SET_PDEATHSIG
    except Exception:
        pass


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _forward(envelope: dict) -> dict:
    req_id = envelope.get("id")
    if not _TOKEN or not _SESSION:
        return _err(req_id, -32603, "bridge misconfigured: missing token or session id")
    req = urllib.request.Request(
        f"{_URL}/api/internal/mcp/{_SESSION}/rpc",
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return _err(req_id, -32603, f"HTTP {e.code}: {body[:200]}")
    except urllib.error.URLError as e:
        return _err(req_id, -32603, f"transport error: {e.reason}")
    except Exception as e:  # noqa: BLE001
        return _err(req_id, -32603, f"bridge error: {e}")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return _err(req_id, -32603, f"invalid JSON response: {body[:200]}")


def main() -> int:
    _arm_parent_death_signal()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"ragb bridge: malformed JSON: {line[:120]}\n")
            sys.stderr.flush()
            continue
        sys.stdout.write(json.dumps(_forward(envelope), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
