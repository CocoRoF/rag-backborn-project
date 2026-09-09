"""Embedding providers. Public HTTP APIs only — no local model is ever downloaded."""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ragb.models._types import EMBED_DIM
from ragb.providers.http import request
from ragb.services import settings as S


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dim: int

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]: ...


class OpenAIEmbedding:
    provider = "openai"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", dim: int = 1536, batch: int = 96):
        self.key, self.model, self.dim, self.batch = api_key, model, dim, batch

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            batch = [t[:24000] or " " for t in texts[i:i + self.batch]]
            r = await request("POST", "https://api.openai.com/v1/embeddings",
                              headers={"Authorization": f"Bearer {self.key}"},
                              json={"model": self.model, "input": batch, "dimensions": self.dim})
            out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
        return out


class GeminiEmbedding:
    provider = "gemini"

    def __init__(self, api_key: str, model: str = "text-embedding-004", dim: int = 768, batch: int = 64):
        self.key, self.model, self.dim, self.batch = api_key, model, dim, batch

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            batch = texts[i:i + self.batch]
            r = await request(
                "POST", f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents",
                params={"key": self.key},
                json={"requests": [{"model": f"models/{self.model}", "taskType": task,
                                    "content": {"parts": [{"text": t[:20000] or " "}]}} for t in batch]})
            out.extend(e["values"] for e in r.json()["embeddings"])
        return out


class VoyageEmbedding:
    provider = "voyage"

    def __init__(self, api_key: str, model: str = "voyage-3", dim: int = 1024, batch: int = 64):
        self.key, self.model, self.dim, self.batch = api_key, model, dim, batch

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            r = await request("POST", "https://api.voyageai.com/v1/embeddings",
                              headers={"Authorization": f"Bearer {self.key}"},
                              json={"model": self.model, "input": texts[i:i + self.batch],
                                    "input_type": "query" if kind == "query" else "document"})
            out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
        return out


class HashEmbedding:
    """Deterministic offline fallback: keyword-bag vectors. Useful for a keyless install or a
    test run, and honestly labelled — it is lexical, not semantic."""
    provider = "hash"

    def __init__(self, dim: int = 1536):
        self.model, self.dim = "hash", dim

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in (t or "").lower().split():
                v[int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class EmbeddingUnavailable(Exception):
    code = "embedding_key_missing"

    def __init__(self, message: str = "embedding provider not configured"):
        super().__init__(message)


async def get_embedding_provider(db: AsyncSession) -> EmbeddingProvider:
    prov = await S.get(db, "embedding.provider")
    model = await S.get(db, "embedding.model")
    dim = int(await S.get(db, "embedding.dim") or 1536)
    batch = int(await S.get(db, "embedding.batch_size") or 64)
    if prov == "openai":
        key = await S.get(db, "providers.openai.api_key")
        if not key:
            raise EmbeddingUnavailable("OpenAI API 키가 없습니다")
        return OpenAIEmbedding(key, model, dim, batch)
    if prov == "gemini":
        key = await S.get(db, "providers.google.api_key")
        if not key:
            raise EmbeddingUnavailable("Google API 키가 없습니다")
        return GeminiEmbedding(key, model, dim, batch)
    if prov == "voyage":
        key = await S.get(db, "providers.voyage.api_key")
        if not key:
            raise EmbeddingUnavailable("Voyage API 키가 없습니다")
        return VoyageEmbedding(key, model, dim, batch)
    if prov == "hash":
        return HashEmbedding(dim)
    raise EmbeddingUnavailable(f"알 수 없는 임베딩 공급자: {prov}")


def pad(vec: list[float], dim: int = EMBED_DIM) -> list[float]:
    """Every vector lands in the same fixed column, so switching provider is a re-index and
    not a migration. Truncating a longer vector loses tail dimensions; that is the trade."""
    if len(vec) == dim:
        return vec
    return vec[:dim] if len(vec) > dim else vec + [0.0] * (dim - len(vec))


async def available(db: AsyncSession) -> bool:
    try:
        await get_embedding_provider(db)
        return True
    except EmbeddingUnavailable:
        return False
