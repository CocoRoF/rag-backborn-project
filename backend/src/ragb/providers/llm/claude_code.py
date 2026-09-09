"""Claude Code CLI as a chat backend.

The CLI is a complete agent: it runs its own tool loop, so ``native_loop`` is True and the
runner never executes a tool for it. Our tools reach it through a loopback MCP server —
``mcp_bridge.py`` speaks JSON-RPC on stdio and forwards every call to this process's
``/api/internal/mcp/{session}/rpc`` endpoint.

Every built-in tool is switched off (``--tools ""`` plus an explicit deny list). A backbone
that indexes a customer's documents must not also hand the model a shell on the machine
that stores them, and "it only reads files" is not a boundary anyone can audit later.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ragb.config import get_settings
from ragb.core.logging import get_logger
from ragb.providers.llm.base import Msg, ToolSpec, render_transcript

log = get_logger("ragb.llm.cli")
BRIDGE_SERVER = "ragb"

# Named individually rather than trusting `--tools ""` alone: the flag is one release away
# from meaning something slightly different, and the deny list keeps working if it does.
NATIVE_TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Glob", "Grep", "LS",
                "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent", "AgentSearch", "Skill", "EnterPlanMode",
                "ExitPlanMode", "Monitor", "TaskOutput", "TaskStop", "AskUserQuestion", "PushNotification",
                "ScheduleWakeup", "RemoteTrigger", "CronCreate", "CronDelete", "CronList", "SendMessage",
                "ListAgents", "KillShell", "BashOutput", "SendUserFile", "EnterWorktree", "ExitWorktree")


def resolve_binary() -> str:
    b = get_settings().claude_binary or "claude"
    return shutil.which(b) or b


def bridge_script() -> str:
    return str(Path(__file__).resolve().parent / "mcp_bridge.py")


def mcp_config(session_id: str, token: str) -> dict[str, Any]:
    s = get_settings()
    return {"mcpServers": {BRIDGE_SERVER: {
        "type": "stdio", "command": sys.executable, "args": [bridge_script()],
        "env": {"RAGB_MCP_URL": s.internal_api_url, "RAGB_MCP_TOKEN": token,
                "RAGB_MCP_SESSION_ID": session_id, "RAGB_MCP_TIMEOUT_S": "300"},
    }}}


class ClaudeCodeChat:
    provider = "claude_code"
    native_loop = True

    def __init__(self, model: str, *, cli_alias: str | None = None, auth_mode: str = "oauth",
                 api_key: str = "", setup_token: str = "", session_id: str = "", bridge_token: str = ""):
        self.model = model
        # cli_alias is what the CLI is launched with instead of model_id. An alias row means
        # "always the current release"; a pinned row must leave it empty.
        self.launch_model = cli_alias or model
        self.auth_mode, self.api_key, self.setup_token = auth_mode, api_key, setup_token
        self.session_id = session_id or str(uuid.uuid4())
        self.bridge_token = bridge_token

    def _env(self) -> dict[str, str]:
        s = get_settings()
        home = str(s.claude_home.parent) if s.claude_home.name == ".claude" else str(s.claude_home)
        env = {
            "HOME": home,
            "CLAUDE_CONFIG_DIR": str(s.claude_home),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        if self.auth_mode == "api_key" and self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key
        elif self.auth_mode == "setup_token" and self.setup_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.setup_token
        return env

    def _argv(self, system: str, tools: list[ToolSpec]) -> list[str]:
        argv = [resolve_binary(), "-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--model", self.launch_model,
                "--permission-mode", "default", "--tools", ""]
        if system:
            argv += ["--system-prompt", system]
        if tools and self.bridge_token:
            argv += ["--mcp-config", json.dumps(mcp_config(self.session_id, self.bridge_token)),
                     "--strict-mcp-config",
                     "--allowed-tools", f"mcp__{BRIDGE_SERVER}",
                     "--settings", json.dumps({"permissions": {"allow": [f"mcp__{BRIDGE_SERVER}"]}})]
        argv += ["--disallowed-tools", *NATIVE_TOOLS]
        return argv

    async def stream(self, *, system: str, messages: list[Msg], tools: list[ToolSpec],
                     temperature: float, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        # The CLI takes one prompt, not a message array. History is rendered in rather than
        # resumed from a session id: a resumed id that the CLI's state directory no longer
        # holds fails the whole turn, and this way the transcript we show is the transcript
        # it saw.
        prompt = render_transcript(messages)
        argv = self._argv(system, tools)
        cwd = str(get_settings().data_dir / "cli-cwd")
        Path(cwd).mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self._env(), cwd=cwd, limit=8 * 1024 * 1024)
        assert proc.stdin and proc.stdout and proc.stderr
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        stderr_buf: list[str] = []

        async def drain_stderr() -> None:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                stderr_buf.append(line.decode(errors="replace")[:500])
                del stderr_buf[:-20]

        err_task = asyncio.create_task(drain_stderr())
        try:
            async for ev in self._read(proc):
                yield ev
        finally:
            err_task.cancel()
            if proc.returncode is None:
                proc.kill()
            rc = await proc.wait()
            if rc not in (0, None) and not self._saw_result:
                detail = " ".join(stderr_buf)[-400:] or f"exit {rc}"
                raise RuntimeError(f"claude code CLI 실패: {detail}")

    async def _read(self, proc: asyncio.subprocess.Process) -> AsyncIterator[dict[str, Any]]:
        self._saw_result = False
        assert proc.stdout
        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                continue          # a single oversized line: skip it rather than end the turn
            if not raw:
                break
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    d = inner.get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        yield {"type": "text", "text": d["text"]}
                    elif d.get("type") == "thinking_delta" and d.get("thinking"):
                        yield {"type": "thinking", "text": d["thinking"]}
            elif kind == "assistant":
                # Text already arrived as deltas; only the tool calls are new information.
                for block in ((ev.get("message") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        yield {"type": "tool_call", "id": block.get("id", ""),
                               "name": _strip_prefix(block.get("name", "")), "input": block.get("input") or {}}
            elif kind == "user":
                for block in ((ev.get("message") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        yield {"type": "tool_result", "id": block.get("tool_use_id", ""), "name": "",
                               "output": _text_of(block.get("content")), "is_error": bool(block.get("is_error"))}
            elif kind == "result":
                self._saw_result = True
                u = ev.get("usage") or {}
                yield {"type": "usage", "input_tokens": int(u.get("input_tokens", 0) or 0),
                       "output_tokens": int(u.get("output_tokens", 0) or 0)}
                if ev.get("subtype") not in (None, "success") or ev.get("is_error"):
                    raise RuntimeError(f"claude code: {str(ev.get('result') or ev.get('subtype'))[:300]}")


def _strip_prefix(name: str) -> str:
    return name.split("__")[-1] if name.startswith("mcp__") else name


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content or "")
