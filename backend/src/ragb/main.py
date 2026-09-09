from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ragb.api import agents, auth, chat, health, intelligence, internal_mcp, repositories
from ragb.api.admin import router as admin_router
from ragb.config import get_settings
from ragb.core.errors import AppError, app_error_handler
from ragb.core.logging import configure, get_logger
from ragb.core.security import validate_security_settings
from ragb.db.session import session_scope

log = get_logger("ragb.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure()
    validate_security_settings()
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.upload_root.mkdir(parents=True, exist_ok=True)
    import ragb.plugins  # noqa: F401  (importing registers the shipped plug-ins)
    async with session_scope() as db:
        from ragb.services import accounts, catalog, claude_code
        await catalog.seed(db)
        await accounts.seed_admin(db)
        seeded = await accounts.seed_demo_accounts(db)
        if seeded:
            log.info("demo accounts seeded", count=seeded)
        # A recreated volume loses the CLI login; the backup in system_settings puts it back.
        try:
            if await claude_code.restore_credentials(db):
                log.info("claude credentials restored from settings backup")
        except Exception as e:  # noqa: BLE001
            log.warning("claude credential restore failed", err=str(e)[:200])
    log.info("ragb api ready", public_url=s.public_url)
    yield


app = FastAPI(title="RAG Backborn", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.add_exception_handler(AppError, app_error_handler)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(repositories.router)
app.include_router(agents.router)
app.include_router(intelligence.router)
app.include_router(intelligence.meta_router)
app.include_router(chat.router)
app.include_router(admin_router)
app.include_router(internal_mcp.router)


@app.exception_handler(Exception)
async def unhandled(request, exc):  # noqa: ANN001
    log.exception("unhandled", path=str(request.url.path))
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "서버 오류"}})
