"""文件夹浏览 & 扫描目录配置 API。

端点：
  GET  /api/folders?folder_id=0     → 列出指定目录的直接子文件夹
  GET  /api/config/scan-folder      → 读取当前保存的扫描目标文件夹
  POST /api/config/scan-folder      → 保存扫描目标文件夹到配置文件
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cloudpop.models.schemas import FolderEntry, FolderListResponse, ScanFolderConfig
from cloudpop.providers.base import AuthError, ProviderError
from cloudpop.providers.provider_115 import get_provider

logger = logging.getLogger(__name__)
router = APIRouter(tags=["folders"])


class SaveScanFolderRequest(BaseModel):
    folder_id: str
    folder_path: str


class AppConfig(BaseModel):
    scan_folder_id: str
    scan_folder_path: str
    output_dir: str
    base_url: str


# ---------------------------------------------------------------------------
# /api/folders  —  浏览文件夹
# ---------------------------------------------------------------------------


@router.get(
    "/api/folders",
    response_model=FolderListResponse,
    summary="列出指定目录的直接子文件夹",
)
async def list_folders(folder_id: str = "0") -> FolderListResponse:
    """返回 115 网盘中 *folder_id* 下的所有直接子文件夹（不递归）。

    - `folder_id=0` 表示根目录
    - 返回的每个 item 都包含 `id`（可继续用来浏览下一级）和 `name`
    """
    try:
        provider = get_provider("115")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        subfolders = await provider.list_subfolders(folder_id)
    except AuthError as exc:
        logger.error("115 文件夹列表认证失败（folder_id=%s）：%s", folder_id, exc)
        raise HTTPException(
            status_code=401,
            detail=f"115 认证失败（Cookie 可能已过期）：{exc}",
        ) from exc
    except ProviderError as exc:
        logger.error("115 文件夹列表请求失败（folder_id=%s）：%s", folder_id, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 构造当前目录的显示路径（简单用 folder_id 判断是否是根）
    if folder_id in ("0", ""):
        folder_path = "/"
    else:
        # 尽量从 provider 获取完整路径（失败则降级为 id 字符串）
        try:
            segments = await provider.get_folder_path(folder_id)
            folder_path = "/" + "/".join(segments) if segments else "/"
        except Exception:
            folder_path = f"/.../{folder_id}"

    items = [
        FolderEntry(id=fi.id, name=fi.name, parent_id=fi.parent_id)
        for fi in subfolders
    ]
    return FolderListResponse(folder_id=folder_id, folder_path=folder_path, items=items)


@router.get(
    "/api/config",
    response_model=AppConfig,
    summary="读取当前应用配置（扫描目录、输出路径等）",
)
async def get_app_config() -> AppConfig:
    """返回当前有效的应用配置，供 Web UI 展示。"""
    from cloudpop.config import get_settings
    s = get_settings()
    return AppConfig(
        scan_folder_id=s.strm.scan_folder_id,
        scan_folder_path=s.strm.scan_folder_path,
        output_dir=str(s.strm.output_path),
        base_url=s.strm.base_url,
    )


# ---------------------------------------------------------------------------
# /api/config/scan-folder  —  读写扫描目录配置
# ---------------------------------------------------------------------------


@router.get(
    "/api/config/scan-folder",
    response_model=ScanFolderConfig,
    summary="读取当前扫描目标文件夹配置",
)
async def get_scan_folder() -> ScanFolderConfig:
    """返回配置文件中保存的扫描目标文件夹。"""
    from cloudpop.config import get_settings
    s = get_settings()
    return ScanFolderConfig(
        folder_id=s.strm.scan_folder_id,
        folder_path=s.strm.scan_folder_path,
    )


@router.post(
    "/api/config/scan-folder",
    response_model=ScanFolderConfig,
    summary="保存扫描目标文件夹到配置文件",
)
async def save_scan_folder(req: SaveScanFolderRequest) -> ScanFolderConfig:
    """将选定的文件夹持久化到 ~/.cloudpop/config.yaml，后续扫描自动使用此目录。

    传入 `folder_id="0"` 可重置为根目录扫描。
    """
    from cloudpop.config import save_scan_folder as _save

    try:
        _save(req.folder_id, req.folder_path)
    except Exception as exc:
        logger.error("保存扫描目录失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc

    logger.info("扫描目录已更新 folder_id=%s path=%s", req.folder_id, req.folder_path)
    return ScanFolderConfig(folder_id=req.folder_id, folder_path=req.folder_path)
