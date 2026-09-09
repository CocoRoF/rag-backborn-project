"""Live provider probes for the admin console. A stored key that nobody has ever used is a
configuration screen telling a comfortable lie."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.providers.http import request
from ragb.services import claude_code
from ragb.services import settings as S


async def probe(db: AsyncSession, provider: str) -> dict[str, Any]:
    try:
        if provider == "openai":
            key = await S.get(db, "providers.openai.api_key")
            if not key:
                return {"ok": False, "detail": "키 없음"}
            r = await request("GET", "https://api.openai.com/v1/models",
                              headers={"Authorization": f"Bearer {key}"}, retries=1)
            return {"ok": True, "detail": f"모델 {len(r.json().get('data', []))}개"}
        if provider == "anthropic":
            key = await S.get(db, "providers.anthropic.api_key")
            if not key:
                return {"ok": False, "detail": "키 없음"}
            r = await request("GET", "https://api.anthropic.com/v1/models",
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, retries=1)
            return {"ok": True, "detail": f"모델 {len(r.json().get('data', []))}개"}
        if provider == "google":
            key = await S.get(db, "providers.google.api_key")
            if not key:
                return {"ok": False, "detail": "키 없음"}
            r = await request("GET", "https://generativelanguage.googleapis.com/v1beta/models",
                              params={"key": key}, retries=1)
            return {"ok": True, "detail": f"모델 {len(r.json().get('models', []))}개"}
        if provider == "voyage":
            key = await S.get(db, "providers.voyage.api_key")
            if not key:
                return {"ok": False, "detail": "키 없음"}
            # Voyage has no list endpoint; the cheapest real call is a one-token embedding.
            await request("POST", "https://api.voyageai.com/v1/embeddings",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": "voyage-3", "input": ["ping"]}, retries=1)
            return {"ok": True, "detail": "임베딩 호출 성공"}
        if provider == "claude_code":
            st = await claude_code.status(db)
            ok = bool(st["credentials_present"]) and not st["expired"]
            if st["auth_mode"] == "api_key":
                ok = bool(st["has_anthropic_key"])
            elif st["auth_mode"] == "setup_token":
                ok = bool(st["has_setup_token"])
            return {"ok": ok, "detail": f"CLI {st['version']}" + ("" if ok else " · 로그인 필요")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{e.__class__.__name__}: {str(e)[:160]}"}
    return {"ok": False, "detail": "알 수 없는 공급자"}


async def probe_embedding(db: AsyncSession) -> dict[str, Any]:
    from ragb.providers.embedding import EmbeddingUnavailable, get_embedding_provider
    try:
        emb = await get_embedding_provider(db)
        vec = (await emb.embed(["연결 확인"], kind="query"))[0]
        return {"ok": True, "detail": f"{emb.provider}/{emb.model} · {len(vec)}차원"}
    except EmbeddingUnavailable as e:
        return {"ok": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{e.__class__.__name__}: {str(e)[:160]}"}
