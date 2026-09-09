"""One turn, start to finish: retrieve, call the model, stream, persist.

Three retrieval modes and two loop shapes meet here.

  off      — no retrieval at all
  auto     — one search from the question, injected before the model speaks (classic RAG)
  agentic  — the model searches for itself, as many times as it needs

  native loop     — the Claude Code CLI runs its own tool loop over the MCP bridge
  driven loop     — the HTTP providers stop at each tool call and we run the next round

The generator owns its database sessions rather than borrowing the request's: a streaming
response outlives the endpoint that returned it, and a session torn down mid-stream fails
in the one place that is hardest to reproduce.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.logging import get_logger
from ragb.db.session import session_scope
from ragb.models import Agent, Conversation, LlmCall, Message, RetrievalLog, User
from ragb.providers.llm import get_chat_model
from ragb.providers.llm.base import LLMUnavailable, Msg, ToolCall
from ragb.services import catalog, retrieval, runtime
from ragb.services import settings as S
from ragb.services import storage as ST
from ragb.services import tools as T

log = get_logger("ragb.chat")

BASE_PROMPT = """너는 문서 저장소에 연결된 지식 어시스턴트다.

원칙:
- 저장소에서 찾은 내용에만 근거해 답한다. 근거가 없으면 "저장소에서 찾지 못했다"고 분명히 말한다.
- 문장 끝에 근거 번호를 [1] 형식으로 붙인다. 번호는 검색 결과에 붙은 번호를 그대로 쓴다.
- 추측과 일반 상식으로 빈칸을 메우지 않는다. 모르는 것은 모른다고 한다.
- 사용자의 언어로 답한다(한국어 질문에는 한국어로).
- 표·수치·고유명사는 원문 그대로 옮긴다."""

AGENTIC_PROMPT = """
검색 도구 사용:
- 답하기 전에 rag_search 를 최소 한 번 호출한다.
- 첫 검색이 부족하면 다른 표현·상위 개념·하위 개념으로 다시 검색한다(최대 3~4회).
- 문서의 구조가 필요하면 rag_outline, 폭넓은 확인이 필요하면 rag_read 를 쓴다.
- 검색 결과가 계속 비어 있으면 지어내지 말고 없다고 답한다."""


async def _load_context(db: AsyncSession, agent: Agent) -> tuple[dict[str, Any], list[uuid.UUID]]:
    repo_ids = await ST.agent_repository_ids(db, agent.id)
    cfg = await S.rag_config(db, agent.settings or {})
    if agent.top_k:
        cfg["top_k"] = agent.top_k
    return cfg, repo_ids


async def _history(db: AsyncSession, conversation_id: uuid.UUID, limit: int) -> list[Msg]:
    rows = list((await db.execute(select(Message).where(Message.conversation_id == conversation_id)
                                  .order_by(Message.created_at.desc()).limit(limit))).scalars().all())
    return [Msg(role=m.role, content=m.content) for m in reversed(rows) if m.content and m.role in ("user", "assistant")]


def _system_prompt(agent: Agent, agentic: bool, repo_names: list[str]) -> str:
    parts = [BASE_PROMPT]
    if agent.system_prompt.strip():
        parts.append("\n[에이전트 지침]\n" + agent.system_prompt.strip())
    if repo_names:
        parts.append("\n[연결된 저장소] " + ", ".join(repo_names))
    if agentic:
        parts.append(AGENTIC_PROMPT)
    return "\n".join(parts)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def run_turn(*, user_id: uuid.UUID, agent_id: uuid.UUID, conversation_id: uuid.UUID,
                   user_text: str) -> AsyncIterator[str]:
    started = time.monotonic()
    pending: list[dict[str, Any]] = []            # events raised by bridge tool calls
    rt: runtime.Runtime | None = None
    text_out, thinking_out = "", ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    tool_events: list[dict[str, Any]] = []
    error = ""

    async with session_scope() as db:
        agent = await db.get(Agent, agent_id)
        user = await db.get(User, user_id)
        conv = await db.get(Conversation, conversation_id)
        if agent is None or conv is None or user is None:
            yield _sse({"type": "error", "message": "대화를 찾을 수 없습니다"})
            return
        cfg, repo_ids = await _load_context(db, agent)
        repo_names = []
        if repo_ids:
            from ragb.models import Repository
            repo_names = [r.name for r in (await db.execute(
                select(Repository).where(Repository.id.in_(repo_ids)))).scalars().all()]
        history = await _history(db, conv.id, int(await S.get(db, "chat.history_messages") or 12))
        max_rounds = int(await S.get(db, "chat.max_tool_rounds") or 6)
        row, fell_back = await catalog.resolve(db, agent.provider, agent.model)
        provider = row.provider if row else agent.provider
        model_id = row.model_id if row else agent.model
        cli_alias = row.cli_alias if row else None
        db.add(Message(conversation_id=conv.id, role="user", content=user_text,
                       created_at=datetime.now(UTC)))
        conv.message_count += 1
        if not conv.title:
            conv.title = user_text.strip().split("\n")[0][:80]

    mode = agent.retrieval_mode if repo_ids else "off"
    agentic = mode == "agentic"
    ctx = T.TurnContext(user_id=user_id, agent_id=agent_id, conversation_id=conversation_id,
                        repository_ids=repo_ids, cfg=cfg)

    yield _sse({"type": "start", "provider": provider, "model": model_id, "mode": mode,
                "fallback_model": fell_back})

    messages = history + [Msg(role="user", content=user_text)]

    # ── auto mode: one search up front, injected as context ─────────────────────
    if mode == "auto":
        async with session_scope() as db:
            res = await retrieval.search(db, repository_ids=repo_ids, query=user_text, cfg=cfg)
        ctx.searches, ctx.candidates, ctx.legs, ctx.reranked = 1, res.candidates, res.legs, res.reranked
        if res.passages:
            ctx.top_score = res.passages[0].score
        ctx.add(res.passages)
        yield _sse({"type": "tool", "phase": "end", "name": "rag_search",
                    "summary": f"{len(res.passages)}건 / 후보 {res.candidates}건 · {res.latency_ms}ms"})
        block = retrieval.format_context(res.passages)
        messages[-1] = Msg(role="user", content=(f"{block}\n\n[질문]\n{user_text}" if block else user_text))

    tool_specs = T.TOOL_SPECS if agentic else []

    try:
        # The CLI reaches our tools over the loopback bridge, so its turn must be findable
        # by session id before the process starts.
        if provider == "claude_code" and agentic:
            rt = runtime.open_runtime(ctx, on_event=pending.append)
        async with session_scope() as db:
            chat_model = await get_chat_model(
                db, provider, model_id, cli_alias=cli_alias,
                session_id=rt.session_id if rt else "", bridge_token=rt.token if rt else "")

        system = _system_prompt(agent, agentic, repo_names)
        rounds = 0
        while True:
            rounds += 1
            calls: list[ToolCall] = []
            round_text = ""
            async for ev in chat_model.stream(system=system, messages=messages, tools=tool_specs,
                                              temperature=agent.temperature, max_tokens=agent.max_tokens):
                while pending:
                    e = pending.pop(0)
                    tool_events.append(e)
                    yield _sse(e)
                kind = ev.get("type")
                if kind == "text":
                    round_text += ev["text"]
                    text_out += ev["text"]
                    yield _sse({"type": "text", "text": ev["text"]})
                elif kind == "thinking":
                    thinking_out += ev["text"]
                    yield _sse({"type": "thinking", "text": ev["text"]})
                elif kind == "tool_call":
                    call = ToolCall(id=ev.get("id") or f"call_{rounds}", name=ev.get("name", ""),
                                    input=ev.get("input") or {})
                    if not chat_model.native_loop:
                        calls.append(call)
                    elif rt is None:
                        # A native-loop model with no bridge cannot reach our tools; report the
                        # attempt so the UI is not silently missing it. With a bridge the
                        # runtime already emitted a precise start/end pair for this same call,
                        # and echoing the CLI's copy would double every row.
                        e = {"type": "tool", "phase": "start", "name": call.name, "input": _preview(call.input)}
                        tool_events.append(e)
                        yield _sse(e)
                elif kind == "tool_result" and chat_model.native_loop and rt is None:
                    e = {"type": "tool", "phase": "end", "name": ev.get("name") or "",
                         "summary": _preview(ev.get("output"), 160), "is_error": bool(ev.get("is_error"))}
                    tool_events.append(e)
                    yield _sse(e)
                elif kind == "usage":
                    usage["input_tokens"] = max(usage["input_tokens"], int(ev.get("input_tokens", 0)))
                    usage["output_tokens"] = max(usage["output_tokens"], int(ev.get("output_tokens", 0)))
            while pending:
                e = pending.pop(0)
                tool_events.append(e)
                yield _sse(e)

            if not calls or chat_model.native_loop:
                break
            if rounds >= max_rounds:
                yield _sse({"type": "notice", "message": f"도구 호출 {max_rounds}회 한도에 도달했습니다"})
                break
            messages.append(Msg(role="assistant", content=round_text, tool_calls=calls))
            for call in calls:
                e = {"type": "tool", "phase": "start", "name": call.name, "input": _preview(call.input)}
                tool_events.append(e)
                yield _sse(e)
                async with session_scope() as db:
                    out, is_err = await T.execute(db, ctx, call.name, call.input)
                e = {"type": "tool", "phase": "end", "name": call.name,
                     "summary": _preview(out, 160), "is_error": is_err}
                tool_events.append(e)
                yield _sse(e)
                messages.append(Msg(role="tool", content=out, tool_call_id=call.id,
                                    tool_name=call.name, is_error=is_err))
    except LLMUnavailable as e:
        error = str(e)
        yield _sse({"type": "error", "message": error, "code": "llm_unavailable"})
    except Exception as e:  # noqa: BLE001
        error = f"{e.__class__.__name__}: {str(e)[:400]}"
        log.warning("turn failed", agent=str(agent_id), err=error)
        yield _sse({"type": "error", "message": error})
    finally:
        if rt is not None:
            runtime.close(rt.session_id)

    latency = int((time.monotonic() - started) * 1000)
    citations = [p.as_dict() for p in ctx.citations]
    if citations:
        yield _sse({"type": "citations", "items": citations})

    async with session_scope() as db:
        msg = Message(conversation_id=conversation_id, role="assistant", content=text_out,
                      thinking=thinking_out[:20000], citations=citations, tool_events=tool_events[-40:],
                      tokens_in=usage["input_tokens"], tokens_out=usage["output_tokens"],
                      latency_ms=latency, error=error[:2000], created_at=datetime.now(UTC))
        db.add(msg)
        conv = await db.get(Conversation, conversation_id)
        if conv is not None:
            conv.message_count += 1
            conv.updated_at = datetime.now(UTC)
        db.add(LlmCall(user_id=user_id, agent_id=agent_id, conversation_id=conversation_id, purpose="chat",
                       provider=provider, model=model_id, tokens_in=usage["input_tokens"],
                       tokens_out=usage["output_tokens"], tool_calls=len([e for e in tool_events if e.get("phase") == "start"]),
                       latency_ms=latency, status="error" if error else "ok", error=error[:2000],
                       created_at=datetime.now(UTC)))
        if ctx.searches:
            db.add(RetrievalLog(user_id=user_id, agent_id=agent_id, conversation_id=conversation_id,
                                query=user_text[:2000], repository_ids=[str(r) for r in repo_ids], legs=ctx.legs,
                                candidates=ctx.candidates, returned=len(ctx.citations), top_score=ctx.top_score,
                                reranked=ctx.reranked, latency_ms=latency,
                                passages=[{"path": p.node_path, "heading": p.heading_path,
                                           "score": round(p.score, 4), "legs": p.legs} for p in ctx.citations[:20]],
                                created_at=datetime.now(UTC)))
        await db.flush()
        message_id = str(msg.id)

    yield _sse({"type": "done", "message_id": message_id, "usage": usage, "latency_ms": latency,
                "searches": ctx.searches, "candidates": ctx.candidates})


def _preview(value: Any, limit: int = 120) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(value)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")
