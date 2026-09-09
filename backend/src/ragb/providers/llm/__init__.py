"""Resolve a chat backend from the admin settings. Public APIs only."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.config import get_settings
from ragb.providers.llm.anthropic import AnthropicChat
from ragb.providers.llm.base import ChatModel, LLMUnavailable, Msg, ToolCall, ToolSpec, render_transcript
from ragb.providers.llm.claude_code import ClaudeCodeChat
from ragb.providers.llm.fake import FakeChat
from ragb.providers.llm.gemini import GeminiChat
from ragb.providers.llm.openai import OpenAIChat
from ragb.services import settings as S

PROVIDERS = ("claude_code", "anthropic", "openai", "gemini")


async def get_chat_model(db: AsyncSession, provider: str, model: str, *, cli_alias: str | None = None,
                         session_id: str = "", bridge_token: str = "") -> ChatModel:
    if get_settings().fake_llm:
        return FakeChat(model or "fake-1")
    if provider == "anthropic":
        key = await S.get(db, "providers.anthropic.api_key")
        if not key:
            raise LLMUnavailable("Anthropic API 키가 설정되지 않았습니다")
        return AnthropicChat(key, model)
    if provider == "openai":
        key = await S.get(db, "providers.openai.api_key")
        if not key:
            raise LLMUnavailable("OpenAI API 키가 설정되지 않았습니다")
        return OpenAIChat(key, model)
    if provider == "gemini":
        key = await S.get(db, "providers.google.api_key")
        if not key:
            raise LLMUnavailable("Google API 키가 설정되지 않았습니다")
        return GeminiChat(key, model)
    if provider == "claude_code":
        mode = await S.get(db, "providers.claude_code.auth_mode") or "oauth"
        return ClaudeCodeChat(
            model, cli_alias=cli_alias, auth_mode=mode,
            api_key=await S.get(db, "providers.anthropic.api_key") or "",
            setup_token=await S.get(db, "providers.claude_code.setup_token") or "",
            session_id=session_id, bridge_token=bridge_token)
    raise LLMUnavailable(f"알 수 없는 공급자: {provider}")


__all__ = ["get_chat_model", "ChatModel", "Msg", "ToolCall", "ToolSpec", "LLMUnavailable",
           "PROVIDERS", "render_transcript"]
