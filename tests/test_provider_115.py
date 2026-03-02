"""Tests for the 115 provider."""

from __future__ import annotations

import pytest
import respx
import httpx

from cloudpop.providers.base import AuthError, FileNotFoundError, ProviderError
from cloudpop.providers.provider_115 import Provider115, _BASE_FILES, _BASE_PRO, _BASE_WEB, _BASE_PASSPORT


@pytest.fixture
def cookies() -> dict[str, str]:
    return {"UID": "testuid", "CID": "testcid", "SEID": "testseid"}


@pytest.fixture
def ua() -> str:
    return "Mozilla/5.0 Test"


@pytest.fixture
def mock_client(cookies, ua):
    """Return a Provider115 with an injected test client."""
    client = httpx.AsyncClient(cookies=cookies)
    return Provider115(cookies=cookies, user_agent=ua, client=client)


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_authenticate_success(cookies, ua):
    respx.get(f"{_BASE_PASSPORT}/app/1.0/web/1.0/check/sso").mock(
        return_value=httpx.Response(200, json={"state": True, "data": {"user_id": 123}})
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    result = await provider.authenticate()
    assert result is True
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_authenticate_cookie_invalid(cookies, ua):
    respx.get(f"{_BASE_PASSPORT}/app/1.0/web/1.0/check/sso").mock(
        return_value=httpx.Response(200, json={"state": False, "errno": 990001, "message": "not login"})
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    with pytest.raises(AuthError):
        await provider.authenticate()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_authenticate_403(cookies, ua):
    respx.get(f"{_BASE_PASSPORT}/app/1.0/web/1.0/check/sso").mock(
        return_value=httpx.Response(403)
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    with pytest.raises(AuthError):
        await provider.authenticate()
    await client.aclose()


# ---------------------------------------------------------------------------
# search_videos – pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_search_videos_single_page(cookies, ua):
    respx.get(f"{_BASE_FILES}/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "state": True,
                "data": [
                    {"fid": "1", "n": "movie1.mkv", "s": 2_000_000_000, "pc": "pc1", "cid": "100", "te": 1700000000},
                    {"fid": "2", "n": "movie2.mp4", "s": 1_500_000_000, "pc": "pc2", "cid": "100", "te": 1700001000},
                ],
                "count": 2,
            },
        )
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    files = [fi async for fi in provider.search_videos("0")]
    await client.aclose()

    assert len(files) == 2
    assert files[0].name == "movie1.mkv"
    assert files[0].pickcode == "pc1"
    assert files[1].name == "movie2.mp4"


@pytest.mark.asyncio
@respx.mock
async def test_search_videos_multi_page(cookies, ua):
    """Verify pagination: two pages of 2 items each (total=4)."""
    page1 = {
        "state": True,
        "data": [
            {"fid": "1", "n": "a.mkv", "s": 1_000_000_000, "pc": "pc1", "cid": "10", "te": 1},
            {"fid": "2", "n": "b.mkv", "s": 1_000_000_000, "pc": "pc2", "cid": "10", "te": 2},
        ],
        "count": 4,
    }
    page2 = {
        "state": True,
        "data": [
            {"fid": "3", "n": "c.mkv", "s": 1_000_000_000, "pc": "pc3", "cid": "10", "te": 3},
            {"fid": "4", "n": "d.mkv", "s": 1_000_000_000, "pc": "pc4", "cid": "10", "te": 4},
        ],
        "count": 4,
    }

    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        offset = int(request.url.params.get("offset", 0))
        call_count += 1
        return httpx.Response(200, json=page1 if offset == 0 else page2)

    respx.get(f"{_BASE_FILES}/files").mock(side_effect=side_effect)

    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    files = [fi async for fi in provider.search_videos("0")]
    await client.aclose()

    assert len(files) == 4
    assert call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_search_videos_empty(cookies, ua):
    respx.get(f"{_BASE_FILES}/files").mock(
        return_value=httpx.Response(200, json={"state": True, "data": [], "count": 0})
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    files = [fi async for fi in provider.search_videos("0")]
    await client.aclose()
    assert files == []


# ---------------------------------------------------------------------------
# get_download_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_download_url_success(cookies, ua):
    pickcode = "abc123"
    respx.post(f"{_BASE_PRO}/app/chrome/downurl").mock(
        return_value=httpx.Response(
            200,
            json={
                "state": True,
                "data": {
                    pickcode: {
                        "url": {"url": "https://cdn.115.com/test.mkv?t=123"},
                        "file_name": "test.mkv",
                    }
                },
            },
        )
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    url = await provider.get_download_url(pickcode)
    await client.aclose()
    assert url == "https://cdn.115.com/test.mkv?t=123"


@pytest.mark.asyncio
@respx.mock
async def test_get_download_url_state_false(cookies, ua):
    respx.post(f"{_BASE_PRO}/app/chrome/downurl").mock(
        return_value=httpx.Response(200, json={"state": False, "errno": 990002})
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    with pytest.raises(ProviderError):
        await provider.get_download_url("badcode")
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_download_url_empty_url(cookies, ua):
    pickcode = "empty123"
    respx.post(f"{_BASE_PRO}/app/chrome/downurl").mock(
        return_value=httpx.Response(
            200,
            json={
                "state": True,
                "data": {pickcode: {"url": {"url": ""}, "file_name": "test.mkv"}},
            },
        )
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    with pytest.raises(FileNotFoundError):
        await provider.get_download_url(pickcode)
    await client.aclose()


# ---------------------------------------------------------------------------
# get_folder_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_folder_path(cookies, ua):
    respx.get(f"{_BASE_FILES}/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": [
                    {"cid": "0", "name": "根目录"},
                    {"cid": "111", "name": "Movies"},
                    {"cid": "222", "name": "Avatar (2009)"},
                ],
                "data": [],
                "count": 0,
            },
        )
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    parts = await provider.get_folder_path("222")
    await client.aclose()
    assert parts == ["Movies", "Avatar (2009)"]


@pytest.mark.asyncio
@respx.mock
async def test_get_folder_path_root(cookies, ua):
    """Root folder should return empty list."""
    respx.get(f"{_BASE_FILES}/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": [{"cid": "0", "name": "根目录"}],
                "data": [],
                "count": 0,
            },
        )
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    parts = await provider.get_folder_path("0")
    await client.aclose()
    assert parts == []


# ---------------------------------------------------------------------------
# find_folder_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_find_folder_id_found(cookies, ua):
    respx.get(f"{_BASE_WEB}/files/getid").mock(
        return_value=httpx.Response(200, json={"id": "999", "state": True})
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    fid = await provider.find_folder_id("/Movies")
    await client.aclose()
    assert fid == "999"


@pytest.mark.asyncio
@respx.mock
async def test_find_folder_id_not_found(cookies, ua):
    respx.get(f"{_BASE_WEB}/files/getid").mock(
        return_value=httpx.Response(200, json={"id": None, "state": True})
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    fid = await provider.find_folder_id("/Nonexistent")
    await client.aclose()
    assert fid is None


# ---------------------------------------------------------------------------
# Rate limit handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_raises(cookies, ua):
    from cloudpop.providers.base import RateLimitError
    respx.get(f"{_BASE_FILES}/files").mock(
        return_value=httpx.Response(429)
    )
    client = httpx.AsyncClient(cookies=cookies)
    provider = Provider115(cookies=cookies, user_agent=ua, client=client)
    with pytest.raises(RateLimitError):
        _ = [fi async for fi in provider.search_videos("0")]
    await client.aclose()
