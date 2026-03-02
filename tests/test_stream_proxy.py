"""Tests for the stream proxy endpoint."""

from __future__ import annotations

import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from cloudpop.cache.manager import get_cache, reset_cache
from cloudpop.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_cache():
    """Ensure each test starts with an empty cache."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


CDN_URL = "https://cdn.115.com/test_video.mkv?t=9999&sign=abc"
PICKCODE = "testpickcode123"
DOWNURL_API = "https://proapi.115.com/app/chrome/downurl"

DOWNURL_RESPONSE = {
    "state": True,
    "data": {
        PICKCODE: {
            "url": {"url": CDN_URL},
            "file_name": "test_video.mkv",
        }
    },
}

SAMPLE_VIDEO_BYTES = b"\x00" * 1024  # 1 KB dummy video data


# ---------------------------------------------------------------------------
# Helper: mock CDN responses
# ---------------------------------------------------------------------------


def mock_cdn_ok(route=None):
    return httpx.Response(
        200,
        content=SAMPLE_VIDEO_BYTES,
        headers={
            "Content-Type": "video/x-matroska",
            "Content-Length": str(len(SAMPLE_VIDEO_BYTES)),
            "Accept-Ranges": "bytes",
        },
    )


def mock_cdn_partial(start: int = 0, end: int = 511, total: int = 1024):
    data = SAMPLE_VIDEO_BYTES[start : end + 1]
    return httpx.Response(
        206,
        content=data,
        headers={
            "Content-Type": "video/x-matroska",
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Accept-Ranges": "bytes",
        },
    )


# ---------------------------------------------------------------------------
# Cache miss → fetch URL → proxy
# ---------------------------------------------------------------------------


@respx.mock
def test_stream_cache_miss_fetches_url(client):
    respx.post(DOWNURL_API).mock(return_value=httpx.Response(200, json=DOWNURL_RESPONSE))
    respx.get(CDN_URL).mock(return_value=mock_cdn_ok())

    resp = client.get(f"/stream/115/{PICKCODE}")

    assert resp.status_code == 200
    assert resp.content == SAMPLE_VIDEO_BYTES
    # Verify URL is now cached
    cache = get_cache()
    assert cache.get(f"dl:{PICKCODE}") == CDN_URL


# ---------------------------------------------------------------------------
# Cache hit → skip URL fetch
# ---------------------------------------------------------------------------


@respx.mock
def test_stream_cache_hit_skips_api(client):
    # Pre-populate cache
    get_cache().set(f"dl:{PICKCODE}", CDN_URL)

    # downurl API should NOT be called
    downurl_route = respx.post(DOWNURL_API).mock(return_value=httpx.Response(200, json=DOWNURL_RESPONSE))
    respx.get(CDN_URL).mock(return_value=mock_cdn_ok())

    resp = client.get(f"/stream/115/{PICKCODE}")

    assert resp.status_code == 200
    assert not downurl_route.called


# ---------------------------------------------------------------------------
# HEAD request support
# ---------------------------------------------------------------------------


@respx.mock
def test_head_request_returns_headers_no_body(client):
    """HEAD 请求应返回包含文件元信息的头部，且 body 为空。"""
    respx.post(DOWNURL_API).mock(return_value=httpx.Response(200, json=DOWNURL_RESPONSE))
    respx.head(CDN_URL).mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Type": "video/x-matroska",
                "Content-Length": "1048576",
                "Accept-Ranges": "bytes",
            },
        )
    )

    resp = client.head(f"/stream/115/{PICKCODE}")

    assert resp.status_code == 200
    assert resp.content == b""  # HEAD 返回无 body
    assert resp.headers.get("content-length") == "1048576"
    assert resp.headers.get("accept-ranges", "").lower() == "bytes"


# ---------------------------------------------------------------------------
# Range request forwarding
# ---------------------------------------------------------------------------


@respx.mock
def test_stream_range_request_forwarded(client):
    respx.post(DOWNURL_API).mock(return_value=httpx.Response(200, json=DOWNURL_RESPONSE))

    def cdn_range_handler(request: httpx.Request) -> httpx.Response:
        assert "Range" in request.headers
        assert request.headers["Range"] == "bytes=0-511"
        return mock_cdn_partial(0, 511, 1024)

    respx.get(CDN_URL).mock(side_effect=cdn_range_handler)

    resp = client.get(f"/stream/115/{PICKCODE}", headers={"Range": "bytes=0-511"})

    assert resp.status_code == 206
    assert "content-range" in resp.headers or "Content-Range" in resp.headers


# ---------------------------------------------------------------------------
# 403 upstream → refresh URL and retry
# ---------------------------------------------------------------------------


@respx.mock
def test_stream_upstream_403_refreshes_url(client):
    NEW_CDN_URL = CDN_URL + "&refreshed=1"
    new_downurl_resp = {
        "state": True,
        "data": {PICKCODE: {"url": {"url": NEW_CDN_URL}, "file_name": "test_video.mkv"}},
    }

    # Pre-populate with the "expired" URL
    get_cache().set(f"dl:{PICKCODE}", CDN_URL)

    call_count = {"cdn": 0, "api": 0}

    def cdn_handler(request: httpx.Request) -> httpx.Response:
        call_count["cdn"] += 1
        url = str(request.url)
        if "refreshed" not in url:
            return httpx.Response(403)  # expired
        return mock_cdn_ok()

    def api_handler(request: httpx.Request) -> httpx.Response:
        call_count["api"] += 1
        return httpx.Response(200, json=new_downurl_resp)

    respx.post(DOWNURL_API).mock(side_effect=api_handler)
    respx.get(CDN_URL).mock(side_effect=cdn_handler)
    respx.get(NEW_CDN_URL).mock(return_value=mock_cdn_ok())

    resp = client.get(f"/stream/115/{PICKCODE}")

    assert resp.status_code == 200
    assert call_count["api"] == 1   # URL was refreshed once
    # New URL should be cached
    assert get_cache().get(f"dl:{PICKCODE}") == NEW_CDN_URL


# ---------------------------------------------------------------------------
# Provider error → 503
# ---------------------------------------------------------------------------


@respx.mock
def test_stream_provider_error_returns_503(client):
    respx.post(DOWNURL_API).mock(return_value=httpx.Response(500))

    resp = client.get(f"/stream/115/{PICKCODE}")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Cache manager unit tests
# ---------------------------------------------------------------------------


def test_cache_set_and_get():
    reset_cache()
    cache = get_cache()
    cache.set("k1", "v1", ttl=60)
    assert cache.get("k1") == "v1"


def test_cache_miss_returns_none():
    reset_cache()
    cache = get_cache()
    assert cache.get("nonexistent") is None


def test_cache_delete():
    reset_cache()
    cache = get_cache()
    cache.set("k1", "v1")
    assert cache.delete("k1") is True
    assert cache.get("k1") is None


def test_cache_clear():
    reset_cache()
    cache = get_cache()
    cache.set("a", "1")
    cache.set("b", "2")
    cleared = cache.clear()
    assert cleared == 2
    assert cache.get("a") is None


def test_cache_stats():
    reset_cache()
    cache = get_cache()
    cache.set("x", "y")
    cache.get("x")   # hit
    cache.get("z")   # miss
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


def test_health_endpoint_unconfigured(client):
    resp = client.get("/health")
    data = resp.json()
    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert "115" in data["providers"]


# ---------------------------------------------------------------------------
# Cache API endpoints
# ---------------------------------------------------------------------------


def test_delete_cache_endpoint(client):
    get_cache().set(f"dl:{PICKCODE}", CDN_URL)
    resp = client.delete(f"/api/cache/{PICKCODE}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert get_cache().get(f"dl:{PICKCODE}") is None


def test_clear_cache_endpoint(client):
    get_cache().set("dl:pc1", "url1")
    get_cache().set("dl:pc2", "url2")
    resp = client.delete("/api/cache")
    assert resp.status_code == 200
    assert resp.json()["cleared"] >= 2
