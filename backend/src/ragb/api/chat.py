"""The turn endpoint. One SSE stream per message."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ragb.core.deps import current_user
from ragb.core.errors import Forbidden, NotFound, ValidationFailed
from ragb.db.session import get_session
from ragb.models import Agent, Conversation, User
from ragb.services.chat import run_turn

router = APIRouter(prefix="/api/chat", tags=["chat"])


class TurnIn(BaseModel):
    agent_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    message: str


@router.post("/turn")
async def turn(body: TurnIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    text = (body.message or "").strip()
    if not text:
        raise ValidationFailed("메시지가 비어 있습니다", code="empty_message")
    if len(text) > 20000:
        raise ValidationFailed("메시지가 너무 깁니다", code="message_too_long")
    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise NotFound("에이전트를 찾을 수 없습니다", code="agent_not_found")
    if agent.owner_id != user.id and not user.is_admin:
        raise Forbidden("이 에이전트에 대한 권한이 없습니다", code="agent_forbidden")
    if body.conversation_id:
        conv = await db.get(Conversation, body.conversation_id)
        if conv is None or conv.user_id != user.id:
            raise NotFound("대화를 찾을 수 없습니다", code="conversation_not_found")
    else:
        conv = Conversation(user_id=user.id, agent_id=agent.id, title="")
        db.add(conv)
        await db.flush()
    conversation_id = conv.id
    await db.commit()

    stream = run_turn(user_id=user.id, agent_id=agent.id, conversation_id=conversation_id, user_text=text)
    return StreamingResponse(stream, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        "X-Conversation-Id": str(conversation_id)})
