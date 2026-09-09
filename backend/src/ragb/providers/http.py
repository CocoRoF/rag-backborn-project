from __future__ import annotations

import asyncio
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class ProviderHTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


async def request(method: str, url: str, *, headers: dict | None = None, json: Any = None, data: Any = None,
                  files: Any = None, params: dict | None = None, retries: int = 3, stream: bool = False,
                  timeout: httpx.Timeout | None = None) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
                r = await client.request(method, url, headers=headers, json=json, data=data, files=files, params=params)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                await asyncio.sleep(0.8 * (2 ** attempt))
                continue
            if r.status_code >= 400:
                raise ProviderHTTPError(r.status_code, r.text)
            return r
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = e
            if attempt < retries - 1:
                await asyncio.sleep(0.8 * (2 ** attempt))
                continue
            raise
    raise last or RuntimeError("request failed")
