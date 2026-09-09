"""Live turns, addressable by the loopback MCP bridge.

The Claude Code CLI runs in a subprocess and calls back over HTTP, so a turn in flight has
to be findable from another request. Entries live only as long as the turn: the registry is
a rendezvous point, not a cache.
"""
from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ragb.services.tools import TurnContext


@dataclass
class Runtime:
    session_id: str
    token: str
    ctx: TurnContext
    events: list[dict[str, Any]] = field(default_factory=list)
    on_event: Callable[[dict[str, Any]], None] | None = None

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.on_event:
            self.on_event(event)


_registry: dict[str, Runtime] = {}


def open_runtime(ctx: TurnContext, on_event: Callable[[dict[str, Any]], None] | None = None) -> Runtime:
    rt = Runtime(session_id=str(uuid.uuid4()), token=secrets.token_urlsafe(32), ctx=ctx, on_event=on_event)
    _registry[rt.session_id] = rt
    return rt


def get(session_id: str, token: str) -> Runtime | None:
    rt = _registry.get(session_id)
    if rt is None or not secrets.compare_digest(rt.token, token):
        return None
    return rt


def close(session_id: str) -> None:
    _registry.pop(session_id, None)


def active() -> int:
    return len(_registry)
