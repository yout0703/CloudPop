"""POST /api/scan endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from cloudpop.config import get_settings
from cloudpop.models.schemas import FileEntry, ScanRequest, ScanResponse
from cloudpop.providers.base import AuthError, ProviderError
from cloudpop.providers.provider_115 import get_provider

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/scan", response_model=ScanResponse)
async def scan(req: ScanRequest) -> ScanResponse:
    """List all video files under the given cloud path."""
    settings = get_settings()
    try:
        provider = get_provider(req.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        # 优先级：config 中保存的 scan_folder_id > 路径解析
        if not settings.strm.is_scan_root:
            folder_id: str | None = settings.strm.scan_folder_id
            logger.info("使用已配置的扫描目录 folder_id=%s path=%s",
                        folder_id, settings.strm.scan_folder_path)
        else:
            folder_id = await provider.find_folder_id(req.path)
        if folder_id is None:
            raise HTTPException(status_code=404, detail=f"Path not found: {req.path!r}")

        display_path = (
            settings.strm.scan_folder_path
            if not settings.strm.is_scan_root
            else req.path
        )
        files: list[FileEntry] = []
        async for fi in provider.search_videos(folder_id):
            files.append(
                FileEntry(
                    id=fi.id,
                    pickcode=fi.pickcode,
                    name=fi.name,
                    size=fi.size,
                    path=f"{display_path.rstrip('/')}/{fi.name}",
                )
            )
        return ScanResponse(files=files, total=len(files), total_videos=len(files))

    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
