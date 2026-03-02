"""Cache management endpoints."""

from fastapi import APIRouter

from cloudpop.cache.manager import get_cache
from cloudpop.models.schemas import CacheClearResponse, CacheDeleteResponse

router = APIRouter()


@router.delete("/api/cache", response_model=CacheClearResponse)
async def clear_cache() -> CacheClearResponse:
    cache = get_cache()
    cleared = cache.clear()
    return CacheClearResponse(cleared=cleared)


@router.delete("/api/cache/{pickcode}", response_model=CacheDeleteResponse)
async def delete_cache_entry(pickcode: str) -> CacheDeleteResponse:
    cache = get_cache()
    deleted = cache.delete(f"dl:{pickcode}")
    return CacheDeleteResponse(deleted=deleted)
