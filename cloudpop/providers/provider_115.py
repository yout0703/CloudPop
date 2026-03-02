"""115 网盘 Provider implementation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

from cloudpop.providers.base import (
    AuthError,
    BaseProvider,
    FileInfo,
    FileNotFoundError,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API endpoints (community-reversed, no official guarantee)
# ---------------------------------------------------------------------------
_BASE_WEB = "https://webapi.115.com"
_BASE_PRO = "https://proapi.115.com"
_BASE_PASSPORT = "https://passportapi.115.com"

_BASE_FILES = "http://web.api.115.com"  # webapi.115.com /files 于 2026-03 起对 GET 返回 405

_URL_CHECK_SSO = f"{_BASE_PASSPORT}/app/1.0/web/1.0/check/sso"
_URL_FILES = f"{_BASE_FILES}/files"
_URL_SEARCH = f"{_BASE_WEB}/files/search"
_URL_GETID = f"{_BASE_WEB}/files/getid"
_URL_DOWNURL = f"{_BASE_PRO}/app/chrome/downurl"  # 需 RSA 加密请求/响应

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv",
    ".flv", ".m4v", ".ts", ".m2ts", ".rmvb",
    ".iso", ".bdmv",
}


def _parse_file_info(item: dict) -> FileInfo:
    """Map a raw 115 API item dict to FileInfo."""
    # 115 API 约定：目录只有 cid（自身 ID），文件才有 fid
    has_fid = item.get("fid") is not None
    return FileInfo(
        id=str(item.get("fid", item.get("cid", ""))),
        pickcode=str(item.get("pc", "")),
        name=str(item.get("n", "")),
        size=int(item.get("s", 0)),
        is_dir=not has_fid,
        # 文件的父目录 ID 存在 cid；目录的父目录 ID 存在 pid
        parent_id=str(item.get("cid", "") if has_fid else item.get("pid", "")),
        modified_at=int(item.get("te", item.get("t", 0))),
    )


class Provider115(BaseProvider):
    """115 网盘 provider backed by Cookie auth + Chrome extension API."""

    def __init__(
        self,
        cookies: dict[str, str],
        user_agent: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cookies = cookies
        self._ua = user_agent
        # Allow injecting a mock client in tests.
        self._client = client

    # ------------------------------------------------------------------
    # Client lifecycle helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            cookies=self._cookies,
            headers={
                "User-Agent": self._ua,
                "Referer": "https://115.com/",
                "Accept": "application/json, text/plain, */*",
            },
            follow_redirects=True,
            timeout=30.0,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        json: dict | None = None,
        headers: dict | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        """Execute an HTTP request and return parsed JSON."""
        use_client = client or self._get_client()
        own_client = client is None and self._client is None
        try:
            resp = await use_client.request(
                method, url, params=params, data=data, json=json, headers=headers
            )
        finally:
            if own_client:
                await use_client.aclose()

        if resp.status_code == 429:
            raise RateLimitError("115 API rate limit exceeded")
        if resp.status_code == 403:
            raise AuthError("115 API returned 403 – check Cookie")
        if resp.status_code >= 400:
            raise ProviderError(f"115 API HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        # Some endpoints return {"state": false, "message": "..."}
        if isinstance(data, dict):
            if data.get("state") is False and "errno" in data:
                errno = data["errno"]
                logger.warning("115 API 返回错误 errno=%s url=%s", errno, url)
                if errno in (990001, 40100000):
                    raise AuthError(f"115 auth error errno={errno}")
                raise ProviderError(f"115 API error errno={errno}: {data.get('message', '')}")
        return data

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """Verify cookie validity via passportapi sso check."""
        try:
            data = await self._request("GET", _URL_CHECK_SSO)
            # {"state": true, "data": {"user_id": ...}}
            if data.get("state") is True or data.get("data"):
                logger.info("115 authentication successful")
                return True
            raise AuthError("SSO check returned unexpected response")
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError(f"Authentication failed: {exc}") from exc

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------

    async def list_files(self, folder_id: str = "0") -> AsyncIterator[FileInfo]:
        """Iterate all items in a single folder (non-recursive)."""
        offset = 0
        limit = 100
        client = self._get_client()
        own_client = self._client is None
        try:
            while True:
                params = {
                    "aid": 1,
                    "cid": folder_id,
                    "o": "file_name",
                    "asc": 1,
                    "offset": offset,
                    "limit": limit,
                    "show_dir": 1,
                    "natsort": 1,
                    "format": "json",
                }
                data = await self._request("GET", _URL_FILES, params=params, client=client)
                items = data.get("data", [])
                if not items:
                    break
                for item in items:
                    yield _parse_file_info(item)
                total = int(data.get("count", 0))
                offset += limit
                if offset >= total:
                    break
                await asyncio.sleep(0.2)
        finally:
            if own_client:
                await client.aclose()

    async def search_videos(self, folder_id: str = "0") -> AsyncIterator[FileInfo]:
        """Iterate ALL video files under *folder_id* across all depths.

        Uses recursive directory traversal + client-side extension filtering.
        This is more reliable than type=4 which requires 115 media library indexing.
        """
        client = self._get_client()
        own_client = self._client is None
        try:
            async for fi in self._traverse(folder_id, client):
                if not fi.is_dir and fi.pickcode and Path(fi.name).suffix.lower() in _VIDEO_EXTENSIONS:
                    yield fi
        finally:
            if own_client:
                await client.aclose()

    async def _traverse(
        self, folder_id: str, client: httpx.AsyncClient
    ) -> AsyncIterator[FileInfo]:
        """递归遍历目录树，共享同一个 httpx client。"""
        offset = 0
        limit = 100
        while True:
            params = {
                "aid": 1,
                "cid": folder_id,
                "o": "file_name",
                "asc": 1,
                "offset": offset,
                "limit": limit,
                "show_dir": 1,
                "natsort": 1,
                "format": "json",
            }
            try:
                data = await self._request("GET", _URL_FILES, params=params, client=client)
            except (AuthError, RateLimitError):
                raise  # 认证失败/限流必须向上抛出，不能静默跳过
            except ProviderError as exc:
                logger.warning("跳过无法访问的目录 folder_id=%s: %s", folder_id, exc)
                return
            items = data.get("data", [])
            if not items:
                break
            subdirs: list[str] = []
            for item in items:
                fi = _parse_file_info(item)
                if fi.is_dir:
                    subdirs.append(fi.id)
                else:
                    yield fi
            # 递归进入子目录
            for sub_id in subdirs:
                async for sub_fi in self._traverse(sub_id, client):
                    yield sub_fi
            total = int(data.get("count", 0))
            offset += len(items)
            if offset >= total:
                break
            await asyncio.sleep(0.2)

    async def search_videos_since(
        self, folder_id: str, since_ts: int
    ) -> AsyncIterator[FileInfo]:
        """Yield video files modified at or after *since_ts* (Unix seconds).

        Requests results sorted by modification time descending; stops paging
        once an item's modified_at falls below since_ts.
        """
        offset = 0
        limit = 100
        client = self._get_client()
        own_client = self._client is None
        try:
            while True:
                params = {
                    "cid": folder_id,
                    "type": 4,
                    "limit": limit,
                    "offset": offset,
                    "format": "json",
                    "natsort": 1,
                    "asc": 0,
                    "o": "user_utime",
                }
                data = await self._request("GET", _URL_SEARCH, params=params, client=client)
                items = data.get("data", [])
                if not items:
                    break
                found_old = False
                for item in items:
                    fi = _parse_file_info(item)
                    fi.is_dir = False
                    if fi.modified_at < since_ts:
                        found_old = True
                        break
                    yield fi
                if found_old:
                    break
                total = int(data.get("count", 0))
                offset += len(items)
                if offset >= total:
                    break
                await asyncio.sleep(0.3)
        finally:
            if own_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    async def list_subfolders(self, folder_id: str = "0") -> list[FileInfo]:
        """Return the immediate subdirectories of *folder_id*.

        Uses the /files endpoint with show_dir=1, then keeps only items
        without a ``fid`` key (directories never have a file-id).
        """
        offset = 0
        limit = 100
        result: list[FileInfo] = []
        client = self._get_client()
        own_client = self._client is None
        try:
            while True:
                params = {
                    "aid": 1,
                    "cid": folder_id,
                    "o": "file_name",
                    "asc": 1,
                    "offset": offset,
                    "limit": limit,
                    "show_dir": 1,
                    "natsort": 1,
                    "format": "json",
                }
                data = await self._request("GET", _URL_FILES, params=params, client=client)
                items = data.get("data", [])
                if not items:
                    break
                for item in items:
                    # 115 API: directories have no `fid`, only `cid` as their own id
                    if item.get("fid") is None and item.get("cid"):
                        result.append(FileInfo(
                            id=str(item["cid"]),
                            pickcode="",
                            name=str(item.get("n", "")),
                            size=0,
                            is_dir=True,
                            parent_id=folder_id,
                            modified_at=int(item.get("te", item.get("t", 0))),
                        ))
                total = int(data.get("count", 0))
                offset += limit
                # 用 not items 或 offset >= total 判断（count 有时只计文件数）
                if offset >= total:
                    break
                await asyncio.sleep(0.2)
        finally:
            if own_client:
                await client.aclose()
        return result

    async def get_folder_path(self, cid: str) -> list[str]:
        """Return path segments (excluding root) for *cid*.

        Calls /files?cid=... and reads the ``path`` breadcrumb array from
        the response.
        """
        params = {
            "aid": 1,
            "cid": cid,
            "o": "file_name",
            "asc": 1,
            "offset": 0,
            "limit": 1,
            "show_dir": 0,
            "format": "json",
        }
        data = await self._request("GET", _URL_FILES, params=params)
        path_nodes: list[dict] = data.get("path", [])
        # path_nodes looks like [{"cid": "0", "name": "根目录"}, {"cid":"111", "name": "Movies"}, ...]
        # Skip the root node (cid == "0")
        segments = [node["name"] for node in path_nodes if str(node.get("cid", "0")) != "0"]
        return segments

    async def find_folder_id(self, path: str) -> Optional[str]:
        """Resolve an absolute path string like '/Movies/2024' to folder_id."""
        params = {"path": path}
        try:
            data = await self._request("GET", _URL_GETID, params=params)
            if data.get("id"):
                return str(data["id"])
            return None
        except ProviderError:
            return None

    # ------------------------------------------------------------------
    # Download URL
    # ------------------------------------------------------------------

    async def get_download_url(self, pickcode: str) -> str:
        """Fetch real CDN URL via proapi.115.com/app/chrome/downurl (RSA 加密).

        接口：POST proapi.115.com/app/chrome/downurl
        请求：data={"data": rsa_encrypt(json.dumps({"pickcode": pickcode}))}
        响应：{"state": true, "data": "<RSA 加密的 JSON>"}
              解密后：{pickcode: {"url": {"url": "https://..."}, ...}}

        自 2025/2026 年起此接口需要对请求/响应均做 RSA 处理，使用 p115cipher 实现。
        """
        import json as _json
        from p115cipher import rsa_encrypt, rsa_decrypt

        payload = _json.dumps({"pickcode": pickcode}).encode()
        encrypted_payload = rsa_encrypt(payload).decode("ascii")

        # 必须用空 user-agent，否则 CDN URL 的签名将与后续请求的 UA 绑定导致 403
        raw = await self._request(
            "POST",
            _URL_DOWNURL,
            data={"data": encrypted_payload},
            headers={"user-agent": ""},
        )
        logger.debug("downurl raw response for %s: %s", pickcode, raw)

        if not raw.get("state"):
            errno_val = raw.get("errno", "?")
            raise ProviderError(
                f"downurl returned state=false for pickcode={pickcode} "
                f"errno={errno_val}: {raw.get('error', raw.get('message', ''))}"
            )

        # 解密响应中的加密 data 字段
        encrypted_data = raw.get("data")
        if not encrypted_data:
            raise ProviderError(f"downurl: missing data field for pickcode={pickcode}")

        try:
            if isinstance(encrypted_data, str):
                inner: dict = _json.loads(rsa_decrypt(encrypted_data.encode()))
            else:
                # 有时 115 会在某些账号下直接返回明文 dict（老账号兼容）
                inner = encrypted_data
        except Exception as exc:
            raise ProviderError(
                f"downurl: failed to decrypt response for pickcode={pickcode}: {exc}"
            ) from exc

        # inner 的格式：{pickcode: {"url": {"url": "..."}, "file_size": ..., ...}}
        entry = inner.get(pickcode) or (next(iter(inner.values())) if inner else None)
        if not entry:
            raise ProviderError(f"downurl: no entry for pickcode={pickcode}")

        url_obj = entry.get("url")
        if isinstance(url_obj, dict):
            url = url_obj.get("url", "")
        elif isinstance(url_obj, str):
            url = url_obj
        else:
            url = ""

        if not url:
            raise FileNotFoundError(f"Empty download URL for pickcode={pickcode}")
        return url


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_provider(name: str, **kwargs) -> BaseProvider:
    """Instantiate a provider by name."""
    if name == "115":
        from cloudpop.config import get_settings
        s = get_settings()
        if not s.is_115_configured():
            raise ValueError("115 credentials not configured – please log in first")
        c = s.provider_115.cookies
        cookies: dict[str, str] = {"UID": c.UID, "CID": c.CID, "SEID": c.SEID}
        if c.KID:  # KID 是可选 cookie，但存在时必须发送
            cookies["KID"] = c.KID
        return Provider115(cookies=cookies, user_agent=s.provider_115.user_agent, **kwargs)
    raise ValueError(f"Unknown provider: {name!r}")
