"""Tests for DLNA discovery description endpoints."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from cloudpop.config import reset_settings
from cloudpop.dlna.service import (
    DlnaDeviceInfo,
    build_ssdp_discovery_response,
    build_ssdp_notify,
)
from cloudpop.main import create_app


def _write_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "server": {"host": "127.0.0.1", "port": 19798},
                "strm": {
                    "base_url": "http://192.168.110.117:19798",
                    "output_dir": str(path.parent / "media"),
                },
                "dlna": {
                    "enabled": False,
                    "friendly_name": "CloudPop Test DLNA",
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _write_media(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    (media_root / "Movies").mkdir(parents=True, exist_ok=True)
    (media_root / "RootVideo.strm").write_text(
        "http://192.168.110.117:19798/stream/115/root/RootVideo.mp4\n",
        encoding="utf-8",
    )
    (media_root / "Movies" / "MovieA.strm").write_text(
        "http://192.168.110.117:19798/stream/115/moviea/MovieA.mp4\n",
        encoding="utf-8",
    )


def test_dlna_device_description_route(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        resp = client.get("/dlna/device.xml")

        assert resp.status_code == 200
        assert "MediaServer:1" in resp.text
        assert "CloudPop Test DLNA" in resp.text
        assert "/dlna/content_directory.xml" in resp.text
        assert "DMS-1.50" in resp.text
        assert "/dlna/icon.png" in resp.text
    reset_settings()


def test_dlna_scpd_routes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        content_directory = client.get("/dlna/content_directory.xml")
        connection_manager = client.get("/dlna/connection_manager.xml")

        assert content_directory.status_code == 200
        assert "<scpd" in content_directory.text
        assert "GetSearchCapabilities" in content_directory.text
        assert "GetFeatureList" in content_directory.text
        assert "GetServiceResetToken" in content_directory.text
        assert "BrowseMetadata" in content_directory.text
        assert "<argumentList>" in content_directory.text
        assert "relatedStateVariable" in content_directory.text
        assert connection_manager.status_code == 200
        assert "<scpd" in connection_manager.text
        assert "GetCurrentConnectionInfo" in connection_manager.text
        assert "CurrentConnectionIDs" in connection_manager.text
        assert "ContentFormatMismatch" in connection_manager.text
        assert "<allowedValue>Output</allowedValue>" in connection_manager.text
    reset_settings()


def test_dlna_browse_root_returns_containers_and_items(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    payload = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
      <ObjectID>0</ObjectID>
      <BrowseFlag>BrowseDirectChildren</BrowseFlag>
      <Filter>*</Filter>
      <StartingIndex>0</StartingIndex>
      <RequestedCount>0</RequestedCount>
      <SortCriteria></SortCriteria>
    </u:Browse>
  </s:Body>
</s:Envelope>
"""

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        resp = client.post(
            "/dlna/control/content_directory",
            content=payload,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"',
                "Content-Type": "text/xml; charset=utf-8",
            },
        )

        assert resp.status_code == 200
        assert "Movies" in resp.text
        assert "RootVideo" in resp.text
        assert "video/mp4" in resp.text
        assert "childCount=" in resp.text
        assert "albumArtURI" in resp.text
        assert "dc:date" in resp.text
    reset_settings()


def test_ssdp_discovery_response_contains_upnp_headers() -> None:
    device = DlnaDeviceInfo(
        friendly_name="CloudPop Test DLNA",
        uuid="12345678-1234-1234-1234-123456789abc",
        location="http://192.168.110.117:19798/dlna/device.xml",
        boot_id=100,
        config_id=1,
        nls="12345678-1234-1234-1234-123456789abc",
    )

    payload = build_ssdp_discovery_response(device, "upnp:rootdevice")

    assert "BOOTID.UPNP.ORG: 100" in payload
    assert "CONFIGID.UPNP.ORG: 1" in payload
    assert "ST: upnp:rootdevice" in payload
    assert "SEARCHPORT.UPNP.ORG: 1900" in payload
    assert "01-NLS: 12345678-1234-1234-1234-123456789abc" in payload


def test_ssdp_notify_contains_upnp_headers() -> None:
    device = DlnaDeviceInfo(
        friendly_name="CloudPop Test DLNA",
        uuid="12345678-1234-1234-1234-123456789abc",
        location="http://192.168.110.117:19798/dlna/device.xml",
        boot_id=100,
        config_id=1,
        nls="12345678-1234-1234-1234-123456789abc",
    )

    payload = build_ssdp_notify(device, "ssdp:alive", "upnp:rootdevice")

    assert "NTS: ssdp:alive" in payload
    assert "BOOTID.UPNP.ORG: 100" in payload
    assert "CONFIGID.UPNP.ORG: 1" in payload
    assert "SEARCHPORT.UPNP.ORG: 1900" in payload
    assert "01-NLS: 12345678-1234-1234-1234-123456789abc" in payload


def test_dlna_get_protocol_info(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    payload = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <u:GetProtocolInfo xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1" />
  </s:Body>
</s:Envelope>
"""

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        resp = client.post(
            "/dlna/control/connection_manager",
            content=payload,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"',
                "Content-Type": "text/xml; charset=utf-8",
            },
        )

        assert resp.status_code == 200
        assert "video/mp4" in resp.text
        assert "video/x-matroska" in resp.text
    reset_settings()


def test_dlna_get_sort_capabilities(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    payload = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <u:GetSortCapabilities xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1" />
  </s:Body>
</s:Envelope>
"""

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        resp = client.post(
            "/dlna/control/content_directory",
            content=payload,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#GetSortCapabilities"',
                "Content-Type": "text/xml; charset=utf-8",
            },
        )

        assert resp.status_code == 200
        assert "dc:title" in resp.text
    reset_settings()


def test_dlna_search_returns_matching_items(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    payload = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <u:Search xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
      <ContainerID>0</ContainerID>
      <SearchCriteria>dc:title contains "Root"</SearchCriteria>
      <Filter>*</Filter>
      <StartingIndex>0</StartingIndex>
      <RequestedCount>0</RequestedCount>
      <SortCriteria></SortCriteria>
    </u:Search>
  </s:Body>
</s:Envelope>
"""

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        resp = client.post(
            "/dlna/control/content_directory",
            content=payload,
            headers={
                "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Search"',
                "Content-Type": "text/xml; charset=utf-8",
            },
        )

        assert resp.status_code == 200
        assert "RootVideo" in resp.text
        assert "Movies" not in resp.text
    reset_settings()
