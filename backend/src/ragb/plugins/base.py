"""The plug-in contract.

A plug-in is one step of a business pipeline sitting on top of the RAG store. It declares
what it needs (`fields`), the runtime hands it a scoped context, and it returns counts plus
a human-readable summary. That is the whole interface — deliberately, because a backbone's
job is to make the *seams* obvious, not to guess the domain.

Five kinds, matching the five stages of a technology-commercialisation programme:

    SOURCE   외부·내부 자료 → 문서/레코드          (DATA)
    ENRICH   레코드 → 구조화 속성·분류             (INTELLIGENCE)
    SCORE    레코드 → 다차원 점수 + 근거           (OPPORTUNITY)
    MATCH    레코드 ↔ 레코드 후보 연결             (MATCHING)
    EXPORT   결과 → 산출물(카드·보고서)            (DECISION SUPPORT)

Two rules every implementation follows:

  1. **Idempotent.** A plug-in is re-run whenever data changes, so it upserts on a stable
     `external_id` rather than appending. A pipeline you are afraid to run twice is a
     pipeline nobody runs.
  2. **Evidence or nothing.** Anything a model asserts carries the chunks it came from.
     Evidence bolted on afterwards cannot be audited, and an expert asked to verify an
     unsourced claim is being asked to guess.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.models import Repository


class PluginKind(StrEnum):
    SOURCE = "source"
    ENRICH = "enrich"
    SCORE = "score"
    MATCH = "match"
    EXPORT = "export"


KIND_LABEL = {
    PluginKind.SOURCE: "데이터 수집",
    PluginKind.ENRICH: "구조화·분류",
    PluginKind.SCORE: "평가",
    PluginKind.MATCH: "매칭",
    PluginKind.EXPORT: "산출물",
}


@dataclass
class ConfigField:
    """One configuration input. `type` drives both validation and the admin form widget —
    one declaration, so a plug-in can never render a form that does not match what it reads."""
    key: str
    label: str
    type: str = "text"            # text | textarea | number | bool | select | collection | scorecard | node
    default: Any = None
    hint: str = ""
    options: list[dict[str, str]] = field(default_factory=list)   # [{value,label}] for select
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "type": self.type, "default": self.default,
                "hint": self.hint, "options": self.options, "required": self.required}


@dataclass
class PluginSpec:
    id: str
    kind: PluginKind
    name: str
    description: str
    produces: str = ""
    fields: list[ConfigField] = field(default_factory=list)
    needs_llm: bool = False
    needs_embedding: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value, "kind_label": KIND_LABEL[self.kind],
                "name": self.name, "description": self.description, "produces": self.produces,
                "needs_llm": self.needs_llm, "needs_embedding": self.needs_embedding,
                "fields": [f.as_dict() for f in self.fields],
                "defaults": {f.key: f.default for f in self.fields}}


@dataclass
class RunResult:
    summary: str = ""
    counts: dict[str, int] = field(default_factory=dict)


class PluginError(Exception):
    """A configuration or data problem the operator can fix. Surfaced verbatim on the run."""


@dataclass
class RunContext:
    """What a plug-in is allowed to touch: one repository, its own config, and a log."""
    db: AsyncSession
    repository: Repository
    config: dict[str, Any]
    run_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    _lines: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self._lines.append(message[:2000])
        del self._lines[:-400]

    @property
    def log_text(self) -> str:
        return "\n".join(self._lines)

    def opt(self, key: str, default: Any = None) -> Any:
        value = self.config.get(key, default)
        return default if value in (None, "") else value

    def require(self, key: str) -> Any:
        value = self.config.get(key)
        if value in (None, "", []):
            raise PluginError(f"필수 설정이 비어 있습니다: {key}")
        return value


class Plugin(Protocol):
    spec: PluginSpec

    async def run(self, ctx: RunContext) -> RunResult: ...


_REGISTRY: dict[str, Plugin] = {}


def register(plugin: Plugin) -> Plugin:
    if plugin.spec.id in _REGISTRY:
        raise RuntimeError(f"duplicate plugin id: {plugin.spec.id}")
    _REGISTRY[plugin.spec.id] = plugin
    return plugin


def get(plugin_id: str) -> Plugin | None:
    return _REGISTRY.get(plugin_id)


def catalog() -> list[dict[str, Any]]:
    order = list(PluginKind)
    return [p.spec.as_dict() for p in sorted(_REGISTRY.values(),
                                             key=lambda p: (order.index(p.spec.kind), p.spec.id))]


def loaded() -> list[str]:
    return sorted(_REGISTRY)


AsyncRunner = Callable[[RunContext], Awaitable[RunResult]]
