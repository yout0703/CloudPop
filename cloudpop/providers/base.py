"""Provider abstractions: FileInfo, BaseProvider, exceptions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    id: str               # fid – 文件 ID
    pickcode: str         # pc  – 获取下载 URL 的 pick_code
    name: str             # n   – 文件名（含扩展名）
    size: int             # s   – 字节数
    is_dir: bool          # 是否为文件夹
    parent_id: str        # cid – 父文件夹 ID / 所在文件夹 ID
    modified_at: int      # te  – Unix 时间戳
    path: str = field(default="")  # 解析后的完整路径（由 generator 填充）


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base error for all provider failures."""


class AuthError(ProviderError):
    """Cookie / credentials invalid or expired."""


class FileNotFoundError(ProviderError):  # noqa: A001
    """Requested file / folder does not exist."""


class RateLimitError(ProviderError):
    """Cloud API rate-limit hit."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseProvider(ABC):
    """Interface every cloud provider must implement."""

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    @abstractmethod
    async def authenticate(self) -> bool:
        """Return True if credentials are valid, raise AuthError otherwise."""
        ...

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_files(self, folder_id: str = "0") -> AsyncIterator[FileInfo]:
        """Iterate all files (no sub-dirs) in *folder_id* (single level)."""
        ...

    @abstractmethod
    async def search_videos(self, folder_id: str = "0") -> AsyncIterator[FileInfo]:
        """Iterate ALL video files under *folder_id* across all depths.

        Uses server-side type=4 filter – no client-side extension filtering.
        """
        ...

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_folder_path(self, cid: str) -> list[str]:
        """Return ordered path segments from root to *cid*.

        Example: ["Movies", "Avatar (2009)"]
        """
        ...

    async def batch_resolve_paths(self, cids: set[str]) -> dict[str, str]:
        """Resolve multiple cid → full path concurrently.

        Returns ``{cid: "/Movies/Avatar (2009)"}`` mapping.
        Default implementation dispatches get_folder_path() for each cid.
        Subclasses may override with a more efficient bulk approach.
        """
        import asyncio

        sem = asyncio.Semaphore(5)

        async def _resolve(cid: str) -> tuple[str, str]:
            async with sem:
                parts = await self.get_folder_path(cid)
                return cid, "/" + "/".join(parts) if parts else "/"

        tasks = [_resolve(cid) for cid in cids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        mapping: dict[str, str] = {}
        for r in results:
            if isinstance(r, BaseException):
                continue
            cid, path = r
            mapping[cid] = path
        return mapping

    @abstractmethod
    async def find_folder_id(self, path: str) -> Optional[str]:
        """Resolve an absolute path string to its folder_id.

        Returns None when the path does not exist.
        """
        ...

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_download_url(self, pickcode: str) -> str:
        """Return the (possibly time-limited) CDN download URL."""
        ...

    # ------------------------------------------------------------------
    # Optional
    # ------------------------------------------------------------------

    async def get_file_info(self, file_id: str) -> Optional[FileInfo]:
        """Return FileInfo for a single file, or None if unavailable."""
        return None
