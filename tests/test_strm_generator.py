"""Tests for the STRM generator."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cloudpop.providers.base import FileInfo
from cloudpop.strm.generator import StrmGenerator
from cloudpop.strm.state import GeneratorState


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_fi(
    fid: str,
    name: str,
    pickcode: str,
    cid: str,
    size: int = 2_000_000_000,
    modified_at: int = 1700000000,
) -> FileInfo:
    return FileInfo(
        id=fid,
        pickcode=pickcode,
        name=name,
        size=size,
        is_dir=False,
        parent_id=cid,
        modified_at=modified_at,
    )


async def _mock_search(*files: FileInfo):
    for fi in files:
        yield fi


class MockProvider:
    """Minimal provider for generator tests."""

    def __init__(
        self,
        folder_id: str = "0",
        files: list[FileInfo] | None = None,
        path_map: dict[str, list[str]] | None = None,
    ) -> None:
        self._folder_id = folder_id
        self._files = files or []
        self._path_map = path_map or {}

    async def find_folder_id(self, path: str) -> str | None:
        return self._folder_id

    async def search_videos(self, folder_id: str):
        for fi in self._files:
            yield fi

    async def search_videos_since(self, folder_id: str, since_ts: int):
        for fi in self._files:
            if fi.modified_at >= since_ts:
                yield fi

    async def get_folder_path(self, cid: str) -> list[str]:
        return self._path_map.get(cid, [])

    async def batch_resolve_paths(self, cids: set[str]) -> dict[str, str]:
        result = {}
        for cid in cids:
            parts = self._path_map.get(cid, [])
            result[cid] = "/" + "/".join(parts) if parts else "/"
        return result


# ---------------------------------------------------------------------------
# Basic STRM generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_creates_strm_files(tmp_path):
    files = [
        make_fi("1", "Avatar.mkv", "pc1", "100"),
        make_fi("2", "Inception.mkv", "pc2", "101"),
    ]
    path_map = {
        "100": ["Movies", "Avatar (2009)"],
        "101": ["Movies", "Inception (2010)"],
    }
    provider = MockProvider(folder_id="0", files=files, path_map=path_map)
    state = GeneratorState(state_path=tmp_path / "state.json")

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )
    result = await gen.generate("/")

    assert result.created == 2
    assert result.skipped == 0
    assert result.errors == 0

    # Verify file contents
    strm1 = tmp_path / "plex" / "Movies" / "Avatar (2009)" / "Avatar.strm"
    strm2 = tmp_path / "plex" / "Movies" / "Inception (2010)" / "Inception.strm"
    assert strm1.exists()
    assert strm2.exists()
    assert strm1.read_text().strip() == "http://localhost:19798/stream/115/pc1/Avatar.mkv"
    assert (
        strm2.read_text().strip()
        == "http://localhost:19798/stream/115/pc2/Inception.mkv"
    )


@pytest.mark.asyncio
async def test_generate_skips_existing_unchanged(tmp_path):
    files = [make_fi("1", "Movie.mkv", "pc1", "100")]
    path_map = {"100": ["Movies"]}
    provider = MockProvider(folder_id="0", files=files, path_map=path_map)
    state = GeneratorState(state_path=tmp_path / "state.json")

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )

    # First run
    r1 = await gen.generate("/")
    assert r1.created == 1
    assert r1.skipped == 0

    # Re-create with same state
    gen2 = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )
    # Second run – file unchanged
    r2 = await gen2.generate("/")
    assert r2.created == 0
    assert r2.skipped == 1


@pytest.mark.asyncio
async def test_dry_run_does_not_write(tmp_path):
    files = [make_fi("1", "Movie.mkv", "pc1", "100")]
    path_map = {"100": ["Movies"]}
    provider = MockProvider(folder_id="0", files=files, path_map=path_map)
    state = GeneratorState(state_path=tmp_path / "state.json")

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )
    result = await gen.generate("/", dry_run=True)

    assert result.dry_run is True
    assert result.created == 1
    # No files written
    assert not (tmp_path / "plex").exists()


@pytest.mark.asyncio
async def test_generate_deep_directory(tmp_path):
    """Files nested several levels deep should produce matching STRM paths."""
    files = [make_fi("1", "episode_01.mkv", "pc1", "deep")]
    path_map = {"deep": ["TV", "ShowName", "Season 01"]}
    provider = MockProvider(folder_id="0", files=files, path_map=path_map)
    state = GeneratorState(state_path=tmp_path / "state.json")

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )
    result = await gen.generate("/")

    assert result.created == 1
    strm = tmp_path / "plex" / "TV" / "ShowName" / "Season 01" / "episode_01.strm"
    assert strm.exists()


# ---------------------------------------------------------------------------
# Size filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_size_filter(tmp_path):
    files = [
        make_fi("1", "trailer.mp4", "pc1", "100", size=50 * 1024 * 1024),   # 50 MB – filtered
        make_fi("2", "movie.mkv", "pc2", "100", size=2_000_000_000),         # 2 GB – kept
    ]
    provider = MockProvider(folder_id="0", files=files, path_map={"100": ["Movies"]})
    state = GeneratorState(state_path=tmp_path / "state.json")

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        min_file_size_mb=100,
        state=state,
    )
    result = await gen.generate("/")

    assert result.created == 1
    assert not (tmp_path / "plex" / "Movies" / "trailer.strm").exists()
    assert (tmp_path / "plex" / "Movies" / "movie.strm").exists()


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_uses_since_ts(tmp_path):
    """In incremental mode, only files newer than last_scan should be processed."""
    old_ts = 1700000000
    new_ts = 1700010000
    state = GeneratorState(state_path=tmp_path / "state.json")
    state.set_last_scan("/", old_ts)
    state.save()

    files = [
        make_fi("1", "old.mkv", "pc1", "100", modified_at=old_ts - 1),      # older
        make_fi("2", "new.mkv", "pc2", "100", modified_at=new_ts),           # newer
    ]
    provider = MockProvider(folder_id="0", files=files, path_map={"100": ["Movies"]})

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )
    result = await gen.generate("/", incremental=True)

    # Only new.mkv should be processed
    assert result.created == 1
    assert (tmp_path / "plex" / "Movies" / "new.strm").exists()
    assert not (tmp_path / "plex" / "Movies" / "old.strm").exists()


@pytest.mark.asyncio
async def test_incremental_reuses_path_map(tmp_path):
    """Second incremental run should not re-fetch known folder paths."""
    state = GeneratorState(state_path=tmp_path / "state.json")
    state.update_path_map({"100": "/Movies"})
    state.set_last_scan("/", 0)
    state.save()

    files = [make_fi("1", "movie.mkv", "pc1", "100", modified_at=1)]
    fetch_count = 0

    class TrackingProvider(MockProvider):
        async def batch_resolve_paths(self, cids: set[str]) -> dict[str, str]:
            nonlocal fetch_count
            fetch_count += len(cids)
            return await super().batch_resolve_paths(cids)

    provider = TrackingProvider(folder_id="0", files=files, path_map={"100": ["Movies"]})

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=tmp_path / "plex",
        state=state,
    )
    await gen.generate("/", incremental=True)

    # cid "100" was already in state, so batch_resolve_paths should not be called
    assert fetch_count == 0


# ---------------------------------------------------------------------------
# Cleanup orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_removes_orphans(tmp_path):
    plex = tmp_path / "plex"
    # Pre-create an "orphan" STRM
    orphan = plex / "Movies" / "Orphan.strm"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("http://localhost:19798/stream/115/old_pc")

    files = [make_fi("1", "Current.mkv", "pc1", "100")]
    provider = MockProvider(folder_id="0", files=files, path_map={"100": ["Movies"]})
    state = GeneratorState(state_path=tmp_path / "state.json")

    gen = StrmGenerator(
        provider=provider,
        base_url="http://localhost:19798",
        output_dir=plex,
        state=state,
    )
    result = await gen.generate("/", cleanup=True)

    assert result.created == 1
    assert not orphan.exists()  # orphan removed
    assert (plex / "Movies" / "Current.strm").exists()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def test_state_last_scan_persists(tmp_path):
    state_path = tmp_path / "state.json"
    s = GeneratorState(state_path=state_path)
    ts = int(time.time())
    s.set_last_scan("/Movies", ts)
    s.save()

    s2 = GeneratorState(state_path=state_path)
    assert s2.get_last_scan("/Movies") == ts


def test_state_path_map_persists(tmp_path):
    state_path = tmp_path / "state.json"
    s = GeneratorState(state_path=state_path)
    s.update_path_map({"100": "/Movies", "200": "/TV"})
    s.save()

    s2 = GeneratorState(state_path=state_path)
    assert s2.get_cached_path("100") == "/Movies"
    assert s2.get_cached_path("200") == "/TV"
    assert s2.get_cached_path("999") is None
