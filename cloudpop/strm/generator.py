"""STRM file generator: three-phase search + path-resolve + write."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from cloudpop.providers.base import BaseProvider, FileInfo
from cloudpop.strm.state import GeneratorState

logger = logging.getLogger(__name__)

COPY_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}


@dataclass
class GenerateResult:
    created: int = 0
    skipped: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    dry_run: bool = False
    error_details: list[str] = field(default_factory=list)


class StrmGenerator:
    """Generate .strm files from a cloud provider directory."""

    def __init__(
        self,
        provider: BaseProvider,
        base_url: str,
        output_dir: Path,
        min_file_size_mb: int = 0,
        state: GeneratorState | None = None,
    ) -> None:
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._output_dir = output_dir
        self._min_bytes = min_file_size_mb * 1024 * 1024
        self._state = state or GeneratorState()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def generate(
        self,
        cloud_path: str,
        incremental: bool = False,
        dry_run: bool = False,
        cleanup: bool = False,
        folder_id: str | None = None,
    ) -> GenerateResult:
        """Run the three-phase STRM generation.

        Phase 1 – Search all video files under cloud_path.
        Phase 2 – Batch-resolve folder paths (cid → full path).
        Phase 3 – Write .strm files to output_dir.

        ``folder_id`` 可直接指定 115 目录 ID，跳过路径解析（Web UI 选择文件夹后使用）。
        """
        t0 = time.monotonic()
        result = GenerateResult(dry_run=dry_run)

        # ──────────────────────────────────────────────────────────────
        # Resolve cloud root folder id
        # ──────────────────────────────────────────────────────────────
        if folder_id is not None:
            # 直接使用传入的 folder_id，跳过路径解析
            root_folder_id = folder_id
        elif cloud_path in ("", "/", "0"):
            root_folder_id = "0"
        else:
            root_folder_id = await self._provider.find_folder_id(cloud_path)
            if root_folder_id is None:
                result.errors += 1
                result.error_details.append(f"Cloud path not found: {cloud_path!r}")
                result.duration_seconds = time.monotonic() - t0
                return result

        # ──────────────────────────────────────────────────────────────
        # Phase 1 – collect video files
        # ──────────────────────────────────────────────────────────────
        logger.info(
            "Phase 1: searching videos in cloud_path=%r (folder_id=%s, incremental=%s)",
            cloud_path,
            root_folder_id,
            incremental,
        )
        since_ts = 0
        if incremental:
            since_ts = self._state.get_last_scan(cloud_path)
            logger.info("Incremental mode: since_ts=%d", since_ts)

        files: list[FileInfo] = []
        try:
            if incremental and since_ts > 0:
                async for fi in self._provider.search_videos_since(root_folder_id, since_ts):
                    files.append(fi)
            else:
                async for fi in self._provider.search_videos(root_folder_id):
                    files.append(fi)
        except Exception as exc:
            result.errors += 1
            result.error_details.append(f"Search error: {exc}")
            result.duration_seconds = time.monotonic() - t0
            return result

        logger.info("Phase 1 done: %d video files found", len(files))

        # Apply size filter
        if self._min_bytes > 0:
            before = len(files)
            files = [f for f in files if f.size >= self._min_bytes]
            logger.info(
                "Size filter (%d MB): %d → %d files",
                self._min_bytes // (1024 * 1024),
                before,
                len(files),
            )

        if not files:
            result.duration_seconds = time.monotonic() - t0
            return result

        # ──────────────────────────────────────────────────────────────
        # Phase 2 – resolve cid → path
        # ──────────────────────────────────────────────────────────────
        logger.info("Phase 2: resolving %d unique folder IDs", len({f.parent_id for f in files}))

        unique_cids = {f.parent_id for f in files}
        # Use cached paths, only fetch missing ones
        cached_map = self._state.get_path_map()
        missing_cids = unique_cids - set(cached_map.keys())

        if missing_cids:
            logger.info("Fetching %d uncached folder paths", len(missing_cids))
            new_map = await self._provider.batch_resolve_paths(missing_cids)
            self._state.update_path_map(new_map)
            cached_map = self._state.get_path_map()

        logger.info("Phase 2 done: path map has %d entries", len(cached_map))

        # ──────────────────────────────────────────────────────────────
        # Phase 3 – write STRM files
        # ──────────────────────────────────────────────────────────────
        logger.info("Phase 3: writing STRM files to %s", self._output_dir)
        known_strm_paths: set[Path] = set()

        for fi in files:
            try:
                strm_path = self._build_strm_path(fi, cached_map)
                known_strm_paths.add(strm_path)
                content = f"{self._build_stream_url(fi)}\n"
                wrote = self._write_strm(strm_path, content, dry_run)
                if wrote:
                    result.created += 1
                    logger.debug("Created: %s", strm_path)
                else:
                    result.skipped += 1
            except Exception as exc:
                result.errors += 1
                result.error_details.append(f"{fi.name}: {exc}")
                logger.warning("Error writing STRM for %s: %s", fi.name, exc)

        # ──────────────────────────────────────────────────────────────
        # Cleanup orphan STRM files
        # ──────────────────────────────────────────────────────────────
        if cleanup and not dry_run:
            self._cleanup_orphans(known_strm_paths, result)

        # ──────────────────────────────────────────────────────────────
        # Persist state
        # ──────────────────────────────────────────────────────────────
        if not dry_run:
            self._state.set_last_scan(cloud_path, int(time.time()))
            self._state.save()

        result.duration_seconds = time.monotonic() - t0
        logger.info(
            "Generation complete: created=%d skipped=%d errors=%d (%.1fs)",
            result.created,
            result.skipped,
            result.errors,
            result.duration_seconds,
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_strm_path(self, fi: FileInfo, path_map: dict[str, str]) -> Path:
        """Compute local .strm path for a FileInfo."""
        folder_path = path_map.get(fi.parent_id, "")
        # folder_path looks like "/Movies/Avatar (2009)" or ""
        relative = folder_path.lstrip("/")
        stem = Path(fi.name).stem
        if relative:
            return self._output_dir / relative / f"{stem}.strm"
        return self._output_dir / f"{stem}.strm"

    def _build_stream_url(self, fi: FileInfo) -> str:
        """Build a stable stream URL that includes the original filename suffix."""
        quoted_name = quote(fi.name, safe="")
        return f"{self._base_url}/stream/115/{fi.pickcode}/{quoted_name}"

    def _write_strm(self, path: Path, content: str, dry_run: bool) -> bool:
        """Write STRM file; return True if file was created/updated, False if skipped."""
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing == content.strip():
                return False  # unchanged

        if not dry_run:
            path.write_text(content, encoding="utf-8")
        return True

    def _cleanup_orphans(self, known: set[Path], result: GenerateResult) -> None:
        """Delete .strm files that no longer exist in the remote library."""
        if not self._output_dir.exists():
            return
        for strm_file in self._output_dir.rglob("*.strm"):
            if strm_file not in known:
                try:
                    strm_file.unlink()
                    logger.info("Cleanup removed orphan: %s", strm_file)
                except OSError as exc:
                    logger.warning("Failed to remove orphan %s: %s", strm_file, exc)
