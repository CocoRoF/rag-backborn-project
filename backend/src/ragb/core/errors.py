from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, code: str | None = None, detail: Any = None, status: int | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code
        if status:
            self.status = status
        self.detail = detail


class ValidationFailed(AppError):
    status, code = 422, "validation_failed"


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Conflict(AppError):
    status, code = 409, "conflict"


class RateLimited(AppError):
    status, code = 429, "rate_limited"


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
    if exc.detail is not None:
        body["error"]["detail"] = exc.detail
    return JSONResponse(status_code=exc.status, content=body)
