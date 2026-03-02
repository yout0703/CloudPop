"""Pydantic request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    provider: str = "115"
    path: str = "/"
    recursive: bool = True


class FileEntry(BaseModel):
    id: str
    pickcode: str
    name: str
    size: int
    path: str


class ScanResponse(BaseModel):
    files: list[FileEntry] = []
    total: int = 0
    total_videos: int = 0  # alias for web UI


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


class FolderEntry(BaseModel):
    id: str
    name: str
    parent_id: str = "0"


class FolderListResponse(BaseModel):
    folder_id: str
    folder_path: str
    items: list[FolderEntry]


class ScanFolderConfig(BaseModel):
    folder_id: str = "0"
    folder_path: str = "/"


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    provider: str = "115"
    cloud_path: str = "/"
    output_path: str = ""
    incremental: bool = False
    dry_run: bool = False
    cleanup: bool = False


class GenerateResponse(BaseModel):
    created: int
    skipped: int
    errors: int
    duration_seconds: float
    dry_run: bool
    output_dir: str = ""
    error_details: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class CacheClearResponse(BaseModel):
    cleared: int


class CacheDeleteResponse(BaseModel):
    deleted: bool


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class ProviderStatus(BaseModel):
    authenticated: bool
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    providers: dict[str, ProviderStatus]
