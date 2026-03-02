"""115 二维码登录流程封装。

API 端点（来源：p115client / 逆向分析）：
  - 获取 token：  GET  https://qrcodeapi.115.com/api/1.0/web/1.0/token/
  - 二维码图片：  GET  https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode?uid={uid}
  - 轮询状态：    GET  https://qrcodeapi.115.com/get/status/?uid={uid}
  - 获取 cookies: POST https://passportapi.115.com/app/1.0/web/1.0/login/qrcode/
  - 验证 cookie:  GET  https://my.115.com/?ct=guide&ac=status
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

QRCODE_API_BASE = "https://qrcodeapi.115.com"
PASSPORT_API_BASE = "https://passportapi.115.com"
STATUS_API_BASE = "https://my.115.com"

# 登录时使用的 User-Agent（115 Web 端）
_LOGIN_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 扫码状态码含义
QR_STATUS_WAITING = 0    # 等待扫描
QR_STATUS_SCANNED = 1    # 已扫码，等待确认
QR_STATUS_CONFIRMED = 2  # 已确认，可获取 cookies
QR_STATUS_EXPIRED = -1   # 二维码已过期
QR_STATUS_CANCELED = -2  # 用户已取消


class QRLoginError(Exception):
    """二维码登录失败。"""


class QRCodeInfo:
    """二维码信息，包含 uid 和图片 URL。"""

    def __init__(self, uid: str, qr_image_url: str, sign: str, time: int) -> None:
        self.uid = uid
        self.qr_image_url = qr_image_url
        self.sign = sign
        self.time = time


async def fetch_qr_token(client: httpx.AsyncClient) -> QRCodeInfo:
    """向 115 申请一个新的二维码登录 token。

    返回包含 uid 和 QR 图片 URL 的 QRCodeInfo 对象。
    """
    url = f"{QRCODE_API_BASE}/api/1.0/web/1.0/token/"
    resp = await client.get(url, headers={"User-Agent": _LOGIN_UA})
    resp.raise_for_status()
    data = resp.json()
    if not data.get("state"):
        raise QRLoginError(f"获取二维码 token 失败：{data}")

    payload = data["data"]
    uid: str = payload["uid"]
    sign: str = payload.get("sign", "")
    time: int = int(payload.get("time", 0))
    qr_image_url = f"{QRCODE_API_BASE}/api/1.0/web/1.0/qrcode?uid={uid}"
    logger.debug("二维码 token 获取成功 uid=%s", uid)
    return QRCodeInfo(uid=uid, qr_image_url=qr_image_url, sign=sign, time=time)


async def poll_qr_status(
    client: httpx.AsyncClient,
    uid: str,
    time: int,
    sign: str,
) -> Literal[0, 1, 2, -1, -2]:
    """轮询二维码扫描状态。

    返回：
      0  等待扫描
      1  已扫码，等待确认
      2  已确认，可获取 cookies
      -1 二维码过期
      -2 用户取消
    """
    url = f"{QRCODE_API_BASE}/get/status/"
    params = {"uid": uid, "time": time, "sign": sign, "_": int(__import__("time").time() * 1000)}
    resp = await client.get(url, params=params, headers={"User-Agent": _LOGIN_UA})
    resp.raise_for_status()
    data = resp.json()
    status: int = data.get("data", {}).get("status", 0)
    logger.debug("二维码状态 uid=%s status=%d", uid, status)
    return status  # type: ignore[return-value]


async def get_cookies_from_qr(
    client: httpx.AsyncClient,
    uid: str,
) -> dict[str, str]:
    """扫码确认成功后，通过 uid 获取 115 登录 cookies。

    返回包含 UID、CID、SEID、KID 字段的字典。
    """
    url = f"{PASSPORT_API_BASE}/app/1.0/web/1.0/login/qrcode/"
    data = {"account": uid}
    resp = await client.post(
        url,
        data=data,
        headers={"User-Agent": _LOGIN_UA},
    )
    resp.raise_for_status()
    payload = resp.json()
    logger.debug("二维码登录响应: %s", payload)

    if not payload.get("state"):
        raise QRLoginError(f"二维码登录失败：{payload}")

    # cookies 既可从响应 JSON 中获取，也可从 Set-Cookie 头中提取
    # 115 通常在 JSON data.cookie 中返回，同时也设置 Set-Cookie
    cookie_data: dict[str, str] = {}

    # 方式一：JSON body 中的 cookie 字段
    body_cookies = payload.get("data", {}).get("cookie", {})
    if body_cookies:
        cookie_data.update({k: str(v) for k, v in body_cookies.items()})

    # 方式二：从响应的 Set-Cookie 头中提取
    if not cookie_data:
        for cookie_name in ("UID", "CID", "SEID", "KID"):
            val = resp.cookies.get(cookie_name)
            if val:
                cookie_data[cookie_name] = val

    if not all(k in cookie_data for k in ("UID", "CID", "SEID")):
        raise QRLoginError(
            f"未能从登录响应中提取完整 cookies，获取到的字段：{list(cookie_data.keys())}"
        )

    logger.info("二维码登录成功，UID=%s", cookie_data.get("UID", "?"))
    return cookie_data


async def verify_cookies(
    client: httpx.AsyncClient,
    cookies: dict[str, str],
    user_agent: str = _LOGIN_UA,
) -> bool:
    """验证 115 cookies 是否有效。"""
    url = f"{STATUS_API_BASE}/?ct=guide&ac=status"
    try:
        resp = await client.get(
            url,
            cookies=cookies,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )
        data = resp.json()
        return bool(data.get("state"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cookie 验证时发生异常：%s", exc)
        return False
