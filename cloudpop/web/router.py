"""Web UI 路由：挂载静态文件，提供页面跳转。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["ui"])

_STATIC_DIR = Path(__file__).parent / "static"


@router.get("/", include_in_schema=False, summary="控制台首页")
async def index() -> FileResponse:
    """返回主控制台页面。"""
    return FileResponse(_STATIC_DIR / "index.html")


@router.get("/login", include_in_schema=False, summary="二维码登录页")
async def login_page() -> FileResponse:
    """返回 115 扫码登录页面。"""
    return FileResponse(_STATIC_DIR / "login.html")
