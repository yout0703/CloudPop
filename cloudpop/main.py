"""FastAPI application factory and startup logic."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cloudpop import __version__

logger = logging.getLogger(__name__)


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    from cloudpop.config import get_settings, reset_settings

    if config_path:
        reset_settings(config_path)
    settings = get_settings()

    # ── Logging ────────────────────────────────────────────────────────
    level = getattr(logging, settings.log.level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # ── Lifespan ───────────────────────────────────────────────────────
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001
        logger.info("CloudPop v%s starting", __version__)
        if not settings.is_115_configured():
            logger.warning(
                "115 credentials not configured. "
                "Set providers.115.cookies in %s",
                "~/.cloudpop/config.yaml",
            )
        from cloudpop.cache.manager import get_cache
        get_cache(default_ttl=settings.cache.download_url_ttl)
        yield

    app = FastAPI(
        title="CloudPop",
        version=__version__,
        description="Multi-provider cloud media bridge for Plex / Skybox VR",
        lifespan=lifespan,
    )

    # ── Global error handlers ──────────────────────────────────────────
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # ── Routers ────────────────────────────────────────────────────────
    from cloudpop.api.health import router as health_router
    from cloudpop.api.scan import router as scan_router
    from cloudpop.api.generate import router as generate_router
    from cloudpop.api.cache import router as cache_router
    from cloudpop.api.auth import router as auth_router
    from cloudpop.api.folders import router as folders_router
    from cloudpop.proxy.stream import router as stream_router
    from cloudpop.web.router import router as web_router

    # Web UI 页面路由放最前（优先级最高，避免被其他路由拦截）
    app.include_router(web_router)
    app.include_router(health_router)
    app.include_router(stream_router)
    app.include_router(auth_router)
    app.include_router(folders_router)
    app.include_router(scan_router)
    app.include_router(generate_router)
    app.include_router(cache_router)

    return app


# Allow running directly with `python -m cloudpop.main`
if __name__ == "__main__":
    import uvicorn
    from cloudpop.config import get_settings

    s = get_settings()
    uvicorn.run(create_app(), host=s.server.host, port=s.server.port)
