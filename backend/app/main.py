from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from .config import settings
from .database import init_db
from .logging_setup import configure_logging
from .routes import assets, chat, configs, jobs, timelines, videos, voice_minimax

configure_logging(settings)
logger = logging.getLogger("hermes")


def create_app() -> FastAPI:
    app = FastAPI(title="Hermes 互动叙事视频", version="0.1.0")

    @app.middleware("http")
    async def http_access_log(request: Request, call_next):
        http_logger = logging.getLogger("hermes.http")
        path = request.url.path
        skip_body = path.startswith("/api/assets/file") or path.startswith("/storage/")
        if not skip_body:
            http_logger.info("→ %s %s", request.method, path)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            http_logger.exception("请求异常 %s %s", request.method, path)
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        if not skip_body:
            http_logger.info(
                "← %s %s %s %.1fms",
                request.method,
                path,
                getattr(response, "status_code", "?"),
                elapsed_ms,
            )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    init_db()

    # 启动时自动 seed 演示数据（首次启动）
    try:
        from .seed import seed

        seed()
    except Exception:  # noqa: BLE001
        logger.exception("seed 失败，将以空数据启动")

    app.include_router(videos.router)
    app.include_router(timelines.router)
    app.include_router(chat.router)
    app.include_router(assets.router)
    app.include_router(jobs.router)
    app.include_router(configs.router)
    app.include_router(voice_minimax.router)

    storage_root = Path(settings.storage_path)
    if storage_root.exists():
        app.mount("/storage", StaticFiles(directory=str(storage_root)), name="storage")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": app.version}

    # 前端构建产物（如果存在则挂载，并支持 SPA fallback）
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(frontend_dist / "assets")),
            name="frontend_assets",
        )

        index_file = frontend_dist / "index.html"

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            # 不要拦截 API / 静态资源
            if full_path.startswith("api/") or full_path.startswith("storage/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = frontend_dist / full_path
            if full_path and candidate.exists() and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(index_file))

    return app


app = create_app()
