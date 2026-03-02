"""115 二维码登录相关 API 端点。

端点：
  POST /api/auth/qr/start         → 申请新二维码，返回 uid 和图片 URL
  GET  /api/auth/qr/status/{uid}  → 轮询扫码状态
  POST /api/auth/qr/confirm       → 扫码成功后保存 cookies（前端在 status=2 后调用）
  GET  /api/auth/status           → 检查当前是否已配置有效 cookies
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cloudpop.auth.qr_login import (
    QR_STATUS_CANCELED,
    QR_STATUS_CONFIRMED,
    QR_STATUS_EXPIRED,
    QR_STATUS_SCANNED,
    QR_STATUS_WAITING,
    QRLoginError,
    fetch_qr_token,
    get_cookies_from_qr,
    poll_qr_status,
    verify_cookies,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# 内存中暂存 QR token 信息（uid → QRCodeInfo），避免重复请求
_qr_tokens: dict[str, object] = {}


class QRStartResponse(BaseModel):
    uid: str
    qr_image_url: str


class QRStatusResponse(BaseModel):
    uid: str
    status: int
    status_text: str


class AuthStatusResponse(BaseModel):
    configured: bool
    uid: str | None = None


class SaveCookiesRequest(BaseModel):
    uid: str


class SaveCookiesResponse(BaseModel):
    success: bool
    message: str


_STATUS_TEXT = {
    QR_STATUS_WAITING: "waiting",
    QR_STATUS_SCANNED: "scanned",
    QR_STATUS_CONFIRMED: "confirmed",
    QR_STATUS_EXPIRED: "expired",
    QR_STATUS_CANCELED: "canceled",
}


@router.post("/qr/start", response_model=QRStartResponse, summary="申请新的二维码登录 token")
async def qr_start() -> QRStartResponse:
    """向 115 申请新的二维码，返回 uid 和可在页面中直接展示的图片 URL。"""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            info = await fetch_qr_token(client)
        except Exception as exc:
            logger.error("申请二维码失败：%s", exc)
            raise HTTPException(status_code=502, detail=f"无法连接 115 服务器：{exc}") from exc

    # 暂存 token 信息（用于轮询时传递 sign/time）
    _qr_tokens[info.uid] = info
    return QRStartResponse(uid=info.uid, qr_image_url=info.qr_image_url)


@router.get(
    "/qr/status/{uid}",
    response_model=QRStatusResponse,
    summary="轮询二维码扫描状态",
)
async def qr_status(uid: str) -> QRStatusResponse:
    """查询指定 uid 的二维码扫描状态。

    前端应每 2 秒调用一次，直到 status 为 2（confirmed）或负数（过期/取消）。
    """
    info = _qr_tokens.get(uid)
    if info is None:
        raise HTTPException(status_code=404, detail="未找到该 uid，请重新申请二维码")

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # 动态获取属性（info 是 QRCodeInfo 对象）
            status = await poll_qr_status(client, uid, getattr(info, "time", 0), getattr(info, "sign", ""))
        except Exception as exc:
            logger.warning("轮询二维码状态失败：%s", exc)
            raise HTTPException(status_code=502, detail=f"轮询失败：{exc}") from exc

    return QRStatusResponse(
        uid=uid,
        status=status,
        status_text=_STATUS_TEXT.get(status, "unknown"),
    )


@router.post(
    "/qr/confirm",
    response_model=SaveCookiesResponse,
    summary="扫码成功后保存 cookies 到配置文件",
)
async def qr_confirm(req: SaveCookiesRequest) -> SaveCookiesResponse:
    """在前端检测到 status=2 后调用此端点，从 115 获取 cookies 并写入配置文件。"""
    uid = req.uid
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            cookies = await get_cookies_from_qr(client, uid)
        except QRLoginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("获取 cookies 失败：%s", exc)
            raise HTTPException(status_code=502, detail=f"获取 cookies 失败：{exc}") from exc

    # 写入配置文件
    from cloudpop.config import save_115_cookies
    try:
        save_115_cookies(cookies)
    except Exception as exc:
        logger.error("保存 cookies 失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"保存配置失败：{exc}") from exc

    # 清理内存中的 token
    _qr_tokens.pop(uid, None)

    return SaveCookiesResponse(success=True, message="登录成功，cookies 已保存")


@router.get("/status", response_model=AuthStatusResponse, summary="检查当前认证状态")
async def auth_status() -> AuthStatusResponse:
    """检查当前配置的 cookies 是否有效。"""
    from cloudpop.config import get_settings

    settings = get_settings()
    if not settings.is_115_configured():
        return AuthStatusResponse(configured=False)

    c = settings.provider_115.cookies
    cookies: dict[str, str] = {"UID": c.UID, "CID": c.CID, "SEID": c.SEID}
    if c.KID:
        cookies["KID"] = c.KID
    ua = settings.provider_115.user_agent

    async with httpx.AsyncClient(timeout=10) as client:
        valid = await verify_cookies(client, cookies, ua)

    return AuthStatusResponse(
        configured=valid,
        uid=c.UID if valid else None,
    )
