"""Stream proxy: GET/HEAD /stream/115/{pickcode}."""

from __future__ import annotations

import logging
import mimetypes
import time
from asyncio import CancelledError
from typing import AsyncIterator
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from cloudpop.cache.manager import get_cache
from cloudpop.providers.base import FileNotFoundError, ProviderError
from cloudpop.providers.provider_115 import get_provider

logger = logging.getLogger(__name__)

router = APIRouter()

# CDN 请求统一使用空 UA，与 downurl 请求保持一致，避免签名绑定 UA 导致 403
_CDN_HEADERS = {"User-Agent": ""}


@router.head("/stream/115/{pickcode}")
@router.head("/stream/115/{pickcode}/{filename:path}")
async def head_115(pickcode: str, filename: str = "") -> Response:
    """HEAD 请求：返回文件大小 / Content-Type，不传输 body。

    Plex、Skybox 等播放器在正式播放前会发送 HEAD 请求探测文件信息。
    若 HEAD 返回 405，播放器会退而发无 Range 的 GET，
    从而触发整个文件下载（即"疯狂下载"现象）。
    """
    cdn_url = await _get_cdn_url(pickcode, refresh=False)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.head(cdn_url, headers=_CDN_HEADERS)
            if resp.status_code in (403, 410):
                # CDN URL 过期，刷新后重试
                cdn_url = await _get_cdn_url(pickcode, refresh=True)
                resp = await client.head(cdn_url, headers=_CDN_HEADERS)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc

    headers = _build_response_headers(resp, cdn_url)
    return Response(status_code=200, headers=headers)


@router.get("/stream/115/{pickcode}")
@router.get("/stream/115/{pickcode}/{filename:path}")
async def stream_115(
    pickcode: str,
    request: Request,
    filename: str = "",
) -> StreamingResponse:
    """GET 请求：代理视频流，正确转发 Range 请求实现分段播放。

    流程：
    1. 从缓存或 115 API 获取 CDN 直链
    2. 带 Range 头向 CDN 请求数据（支持分段，实现流畅 seek）
    3. 上游 403/410 时刷新 URL 并重试一次
    4. 流式返回数据，确保 httpx 客户端在响应结束时被关闭
    """
    cache_key = f"dl:{pickcode}"
    t0 = time.monotonic()

    cdn_url = await _get_cdn_url(pickcode, refresh=False)
    range_header = request.headers.get("Range")

    client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
    try:
        response = await client.send(
            client.build_request("GET", cdn_url, headers={**_CDN_HEADERS, **(
                {"Range": range_header} if range_header else {}
            )}),
            stream=True,
        )

        # CDN URL 过期：刷新后重试一次
        if response.status_code in (403, 410):
            await response.aclose()
            cdn_url = await _get_cdn_url(pickcode, refresh=True)
            response = await client.send(
                client.build_request("GET", cdn_url, headers={**_CDN_HEADERS, **(
                    {"Range": range_header} if range_header else {}
                )}),
                stream=True,
            )
    except (ProviderError, httpx.HTTPError) as exc:
        await client.aclose()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    elapsed = time.monotonic() - t0
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "stream pickcode=%s range=%s status=%d %.0fms client=%s",
        pickcode,
        range_header or "-",
        response.status_code,
        elapsed * 1000,
        client_ip,
    )

    resp_headers = _build_response_headers(response, cdn_url)
    return StreamingResponse(
        _stream_with_cleanup(response, client),
        status_code=response.status_code,
        headers=resp_headers,
        media_type=resp_headers.get("content-type", "application/octet-stream"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_cdn_url(pickcode: str, refresh: bool) -> str:
    """从缓存或 115 API 取 CDN 直链；refresh=True 时强制刷新缓存。"""
    cache = get_cache()
    cache_key = f"dl:{pickcode}"
    if refresh:
        cache.delete(cache_key)
    cdn_url = cache.get(cache_key)
    if cdn_url is None:
        provider = get_provider("115")
        try:
            cdn_url = await provider.get_download_url(pickcode)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"File not found: {exc}") from exc
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=f"Provider error: {exc}") from exc
        cache.set(cache_key, cdn_url)
    return cdn_url


async def _stream_with_cleanup(
    response: httpx.Response,
    client: httpx.AsyncClient,
) -> AsyncIterator[bytes]:
    """逐块 yield 响应数据，无论正常结束还是客户端断开均关闭 httpx 客户端。"""
    try:
        async for chunk in response.aiter_bytes(chunk_size=512 * 1024):
            yield chunk
    except CancelledError:
        # 客户端主动断开（如 seek、停止播放），正常情况，不记录 warning
        pass
    finally:
        await response.aclose()
        await client.aclose()


def _infer_content_type(cdn_url: str) -> str | None:
    """从 CDN URL 的路径部分提取文件名，推断 MIME 类型。"""
    try:
        path = urlsplit(cdn_url).path
        filename = unquote(path.rsplit("/", 1)[-1])
        mime, _ = mimetypes.guess_type(filename)
        return mime
    except Exception:
        return None


def _build_response_headers(response: httpx.Response, cdn_url: str = "") -> dict[str, str]:
    """从上游响应中提取并构建转发给客户端的响应头。"""
    passthrough = {
        "content-type",
        "content-length",
        "content-range",
        "accept-ranges",
        "last-modified",
        "etag",
    }
    # 统一用小写 key 存储，避免大小写不同导致重复头
    result: dict[str, str] = {"accept-ranges": "bytes"}
    for k, v in response.headers.items():
        kl = k.lower()
        if kl in passthrough:
            result[kl] = v

    # 若 CDN 返回 application/octet-stream 或没有 Content-Type，
    # 尝试从 URL 文件名推断更精确的类型（video/mp4 等），
    # 让 Plex 能正确识别格式，避免触发不必要的转码。
    ct = result.get("content-type", "")
    if (not ct or "octet-stream" in ct) and cdn_url:
        inferred = _infer_content_type(cdn_url)
        if inferred:
            result["content-type"] = inferred

    return result
