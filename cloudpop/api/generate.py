"""POST /api/generate endpoint."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from cloudpop.config import get_settings
from cloudpop.models.schemas import GenerateRequest, GenerateResponse
from cloudpop.providers.base import AuthError, ProviderError
from cloudpop.providers.provider_115 import get_provider
from cloudpop.strm.generator import StrmGenerator

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Generate STRM files for the given cloud path."""
    settings = get_settings()

    try:
        provider = get_provider(req.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    output_path = Path(req.output_path).expanduser().resolve() if req.output_path else settings.strm.output_path

    gen = StrmGenerator(
        provider=provider,
        base_url=settings.strm.base_url,
        output_dir=output_path,
        min_file_size_mb=settings.strm.min_file_size_mb,
    )

    # 优先使用配置中保存的 scan_folder_id（通过 Web UI 选择的目标文件夹）
    use_folder_id: str | None = None
    use_cloud_path: str = req.cloud_path
    if not settings.strm.is_scan_root:
        use_folder_id = settings.strm.scan_folder_id
        use_cloud_path = settings.strm.scan_folder_path
        logger.info(
            "使用配置的扫描目录 folder_id=%s path=%s",
            use_folder_id,
            use_cloud_path,
        )

    try:
        result = await gen.generate(
            cloud_path=use_cloud_path,
            incremental=req.incremental,
            dry_run=req.dry_run,
            cleanup=req.cleanup,
            folder_id=use_folder_id,
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return GenerateResponse(
        created=result.created,
        skipped=result.skipped,
        errors=result.errors,
        duration_seconds=round(result.duration_seconds, 2),
        dry_run=result.dry_run,
        output_dir=str(output_path),
        error_details=result.error_details,
    )
