"""Dashboard summary endpoint for the Web UI."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from cloudpop import __version__
from cloudpop.cache.manager import get_cache
from cloudpop.config import get_settings

router = APIRouter(tags=["dashboard"])


class DashboardAuthStatus(BaseModel):
    configured: bool
    uid: str | None = None


class DashboardDlnaStatus(BaseModel):
    enabled: bool
    friendly_name: str
    location: str
    device_xml_url: str
    uuid: str
    containers: int
    items: int


class DashboardCacheStatus(BaseModel):
    size: int
    hits: int
    misses: int
    hit_rate: float


class DashboardResponse(BaseModel):
    status: str
    version: str
    auth: DashboardAuthStatus
    scan_folder_id: str
    scan_folder_path: str
    output_dir: str
    base_url: str
    provider_115_authenticated: bool
    provider_115_error: str | None = None
    dlna: DashboardDlnaStatus
    cache: DashboardCacheStatus


@router.get("/api/dashboard", response_model=DashboardResponse, summary="读取控制台汇总状态")
async def dashboard(request: Request) -> DashboardResponse:
    """聚合 Web UI 展示所需的系统状态。"""
    settings = get_settings()
    provider_authenticated = False
    provider_error: str | None = None

    if settings.is_115_configured():
        from cloudpop.providers.base import AuthError
        from cloudpop.providers.provider_115 import get_provider

        provider = get_provider("115")
        try:
            await provider.authenticate()
            provider_authenticated = True
        except AuthError as exc:
            provider_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            provider_error = str(exc)
    else:
        provider_error = "No credentials configured"

    auth_uid = settings.provider_115.cookies.UID or None
    device_info = request.app.state.dlna_device_info
    library_stats = request.app.state.dlna_library.stats()
    cache_stats = get_cache().stats()

    return DashboardResponse(
        status="ok",
        version=__version__,
        auth=DashboardAuthStatus(
            configured=settings.is_115_configured(),
            uid=auth_uid,
        ),
        scan_folder_id=settings.strm.scan_folder_id,
        scan_folder_path=settings.strm.scan_folder_path,
        output_dir=str(settings.strm.output_path),
        base_url=settings.strm.base_url,
        provider_115_authenticated=provider_authenticated,
        provider_115_error=provider_error,
        dlna=DashboardDlnaStatus(
            enabled=settings.dlna.enabled,
            friendly_name=device_info.friendly_name,
            location=device_info.location,
            device_xml_url=device_info.location,
            uuid=device_info.uuid,
            containers=library_stats["containers"],
            items=library_stats["items"],
        ),
        cache=DashboardCacheStatus(
            size=cache_stats["size"],
            hits=cache_stats["hits"],
            misses=cache_stats["misses"],
            hit_rate=cache_stats["hit_rate"],
        ),
    )
