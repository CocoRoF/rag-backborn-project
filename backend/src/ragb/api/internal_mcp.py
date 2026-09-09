"""Loopback MCP JSON-RPC endpoint. The only caller is our own CLI bridge subprocess.

It is reachable on 127.0.0.1 only in practice (nginx never proxies /api/internal), and every
call still has to present the per-turn bearer token minted when the turn opened.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ragb.core.logging import get_logger
from ragb.db.session import session_scope
from ragb.services import runtime
from ragb.services import tools as T

router = APIRouter(prefix="/api/internal/mcp", tags=["internal"])
log = get_logger("ragb.mcp")
PROTOCOL = "2024-11-05"


def _err(id_: Any, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


@router.post("/{session_id}/rpc")
async def rpc(session_id: str, request: Request):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    rt = runtime.get(session_id, token) if token else None
    if rt is None:
        return JSONResponse(status_code=401, content=_err(None, -32001, "unauthorized"))
    try:
        env = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_err(None, -32700, "parse error"))
    method, id_, params = env.get("method"), env.get("id"), env.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False, "subscribe": False},
                             "prompts": {"listChanged": False}, "logging": {}},
            "serverInfo": {"name": "ragb", "version": "1.0"}}}
    if method in ("notifications/initialized", "logging/setLevel", "ping"):
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"resources": []}}
    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"resourceTemplates": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"prompts": []}}
    if method == "completion/complete":
        return {"jsonrpc": "2.0", "id": id_, "result": {"completion": {"values": [], "total": 0, "hasMore": False}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in T.TOOL_SPECS]}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        rt.emit({"type": "tool", "phase": "start", "name": name, "input": _preview(args)})
        t0 = time.monotonic()
        async with session_scope() as db:
            out, is_err = await T.execute(db, rt.ctx, name, args)
        rt.emit({"type": "tool", "phase": "end", "name": name, "summary": _preview(out, 160),
                 "is_error": is_err, "duration_ms": int((time.monotonic() - t0) * 1000)})
        return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": out}], "isError": is_err}}
    return _err(id_, -32601, f"method not found: {method}")


def _preview(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")
